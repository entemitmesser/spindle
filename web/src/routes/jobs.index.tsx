import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { Disc3, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { z } from "zod";

import { EmptyState } from "@/components/common/EmptyState";
import { MultiSelectFilter } from "@/components/common/MultiSelectFilter";
import { JobActionsMenu } from "@/components/jobs/JobActionsMenu";
import { StageProgress } from "@/components/status/StageProgress";
import { StatusBadge } from "@/components/status/StatusBadge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { type JobStatus, type JobSummary, type MediaKind, api } from "@/lib/api";
import { formatEta, jobDisplayTitle } from "@/lib/format";
import { queryKeys } from "@/lib/query-keys";
import { useJobProgress } from "@/providers/EventProvider";
import { usePageTitle } from "@/providers/PageTitleProvider";

const JOB_STATUSES: JobStatus[] = [
  "queued",
  "running",
  "paused",
  "needs_review",
  "completed",
  "failed",
  "cancelled",
];

const PAGE_SIZE = 20;

const jobsSearchSchema = z.object({
  status: z.array(z.enum(JOB_STATUSES)).optional(),
  media_kind: z.enum(["movie", "series", "music", "other"]).optional(),
  q: z.string().optional(),
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/jobs/")({
  component: JobsPage,
  validateSearch: jobsSearchSchema,
});

function JobsTableRow({ job }: { job: JobSummary }) {
  const live = useJobProgress(job.id);
  const currentTask = live?.taskKind ?? job.current_task;
  const eta = live?.etaSeconds ?? job.eta_seconds;

  return (
    <TableRow>
      <TableCell>
        <Link
          to="/jobs/$jobId"
          params={{ jobId: String(job.id) }}
          className="hover:text-primary font-medium"
        >
          {jobDisplayTitle(job.title, job.year)}
        </Link>
      </TableCell>
      <TableCell>
        <StatusBadge status={job.status} size="sm" />
      </TableCell>
      <TableCell className="w-40">
        <StageProgress jobStatus={job.status} currentTask={currentTask} size="sm" />
      </TableCell>
      <TableCell className="text-muted-foreground text-xs tabular-nums">
        {eta !== null ? formatEta(eta) : "—"}
      </TableCell>
      <TableCell className="text-right">
        <JobActionsMenu job={job} />
      </TableCell>
    </TableRow>
  );
}

function JobsPage() {
  usePageTitle("Jobs");
  const navigate = useNavigate({ from: Route.fullPath });
  const search = Route.useSearch();
  const [qInput, setQInput] = useState(search.q ?? "");

  useEffect(() => {
    const timer = setTimeout(() => {
      if (qInput !== (search.q ?? "")) {
        void navigate({ search: (prev) => ({ ...prev, q: qInput || undefined, page: 0 }) });
      }
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-run when the debounced input itself changes
  }, [qInput]);

  const page = search.page ?? 0;
  const { data, isPending } = useQuery({
    queryKey: queryKeys.jobs.list(search),
    queryFn: () =>
      api.jobs.list({
        status: search.status,
        media_kind: search.media_kind,
        q: search.q,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
  });

  const total = data?.total ?? 0;
  const from = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const to = Math.min(total, (page + 1) * PAGE_SIZE);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
          <Input
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Search jobs…"
            className="w-64 pl-8"
          />
        </div>
        <MultiSelectFilter
          label="Status"
          options={JOB_STATUSES.map((s) => ({ value: s, label: s.replace("_", " ") }))}
          selected={search.status ?? []}
          onChange={(values) =>
            void navigate({
              search: (prev) => ({
                ...prev,
                status: values.length ? (values as JobStatus[]) : undefined,
                page: 0,
              }),
            })
          }
        />
        <Select
          value={search.media_kind ?? "all"}
          onValueChange={(value) =>
            void navigate({
              search: (prev) => ({
                ...prev,
                media_kind: value === "all" ? undefined : (value as MediaKind),
                page: 0,
              }),
            })
          }
        >
          <SelectTrigger size="sm" className="w-32">
            <SelectValue placeholder="Kind" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All kinds</SelectItem>
            <SelectItem value="movie">Movie</SelectItem>
            <SelectItem value="series">Series</SelectItem>
            <SelectItem value="music">Music</SelectItem>
            <SelectItem value="other">Other</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {isPending && (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      )}

      {!isPending && data && data.items.length === 0 && (
        <EmptyState icon={Disc3} title="No jobs match these filters." />
      )}

      {!isPending && data && data.items.length > 0 && (
        <>
          <Table>
            <TableCaption className="sr-only">Ripping and transcoding jobs</TableCaption>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Stage</TableHead>
                <TableHead>ETA</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((job) => (
                <JobsTableRow key={job.id} job={job} />
              ))}
            </TableBody>
          </Table>

          <div className="flex items-center justify-between">
            <p className="text-muted-foreground text-xs">
              {from}–{to} of {total}
            </p>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page === 0}
                onClick={() => void navigate({ search: (prev) => ({ ...prev, page: page - 1 }) })}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={to >= total}
                onClick={() => void navigate({ search: (prev) => ({ ...prev, page: page + 1 }) })}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
