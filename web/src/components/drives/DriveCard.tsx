import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Disc, EllipsisVertical, RefreshCw, Slash, SquareArrowOutUpRight } from "lucide-react";
import { toast } from "sonner";

import { StageProgress } from "@/components/status/StageProgress";
import { StatusBadge } from "@/components/status/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { type DriveOut, ApiError, api } from "@/lib/api";
import { formatEta, jobDisplayTitle } from "@/lib/format";
import { queryKeys } from "@/lib/query-keys";
import { useJobProgress } from "@/providers/EventProvider";
import { cn } from "@/lib/utils";

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong.";
}

function DriveJobLine({ drive }: { drive: DriveOut }) {
  const job = drive.job;
  const live = useJobProgress(job?.id ?? -1);
  if (!job) return null;

  const currentTask = live?.taskKind ?? job.current_task;
  const eta = live?.etaSeconds ?? job.eta_seconds;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <Link
          to="/jobs/$jobId"
          params={{ jobId: String(job.id) }}
          className="hover:text-primary truncate text-sm font-medium transition-colors"
        >
          {jobDisplayTitle(job.title, job.year)}
        </Link>
        {eta !== null && (
          <span className="text-muted-foreground shrink-0 text-xs">{formatEta(eta)}</span>
        )}
      </div>
      <StageProgress jobStatus={job.status} currentTask={currentTask} size="sm" />
    </div>
  );
}

export function DriveCard({ drive }: { drive: DriveOut }) {
  const queryClient = useQueryClient();

  const eject = useMutation({
    mutationFn: () => api.drives.eject(drive.id),
    onSuccess: () => {
      toast.success("Disc ejected.");
      void queryClient.invalidateQueries({ queryKey: queryKeys.drives.list() });
    },
    onError: (error) => toast.error(`Eject failed: ${errorMessage(error)}`),
  });

  const rescan = useMutation({
    mutationFn: () => api.drives.rescan(drive.id),
    onSuccess: () => {
      toast.success("Rescanning drive…");
      void queryClient.invalidateQueries({ queryKey: queryKeys.drives.list() });
    },
    onError: (error) => toast.error(`Rescan failed: ${errorMessage(error)}`),
  });

  const toggleEnabled = useMutation({
    mutationFn: () => api.drives.update(drive.id, { is_enabled: !drive.is_enabled }),
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: queryKeys.drives.list() });
      const previous = queryClient.getQueryData(queryKeys.drives.list());
      queryClient.setQueryData(
        queryKeys.drives.list(),
        (old: { items: DriveOut[] } | undefined) =>
          old && {
            items: old.items.map((d) =>
              d.id === drive.id ? { ...d, is_enabled: !d.is_enabled } : d,
            ),
          },
      );
      return { previous };
    },
    onError: (error, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(queryKeys.drives.list(), context.previous);
      toast.error(`Update failed: ${errorMessage(error)}`);
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: queryKeys.drives.list() }),
  });

  const hasDisc = !!drive.disc;

  return (
    <Card className={cn(drive.status === "error" && "border-status-danger/40")}>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <StatusBadge status={drive.status} />
          </div>
          <p className="font-mono text-sm font-medium">{drive.display_name ?? drive.device_path}</p>
          {drive.model && <p className="text-muted-foreground font-mono text-xs">{drive.model}</p>}
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-sm" aria-label="Drive actions">
              <EllipsisVertical className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem disabled={!hasDisc || eject.isPending} onClick={() => eject.mutate()}>
              <SquareArrowOutUpRight />
              Eject disc
            </DropdownMenuItem>
            <DropdownMenuItem disabled={rescan.isPending} onClick={() => rescan.mutate()}>
              <RefreshCw />
              Rescan
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={toggleEnabled.isPending}
              onClick={() => toggleEnabled.mutate()}
            >
              <Slash />
              {drive.is_enabled ? "Disable drive" : "Enable drive"}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </CardHeader>
      <CardContent className="space-y-3">
        {!hasDisc && (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Disc className="size-4 shrink-0" aria-hidden="true" />
            No disc. Insert one to start.
          </p>
        )}
        {hasDisc && !drive.job && (
          <p className="text-muted-foreground truncate text-sm">
            {drive.disc?.label ?? "Unlabeled disc"}
          </p>
        )}
        {drive.job && <DriveJobLine drive={drive} />}
      </CardContent>
    </Card>
  );
}
