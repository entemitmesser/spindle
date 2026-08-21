import { ChevronDown, Pause, Play, RotateCcw, Trash2, X } from "lucide-react";
import { useState } from "react";

import { PosterImage } from "@/components/common/PosterImage";
import { JobDeleteDialog } from "@/components/jobs/JobDeleteDialog";
import { StageProgress } from "@/components/status/StageProgress";
import { StatusBadge } from "@/components/status/StatusBadge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useJobActions } from "@/hooks/useJobActions";
import type { JobDetail } from "@/lib/api";
import { jobDisplayTitle } from "@/lib/format";
import { TASK_KIND_LABELS, TASK_KIND_ORDER } from "@/lib/stage-progress";

interface JobDetailHeaderProps {
  job: JobDetail;
  onEditIdentity: () => void;
}

export function JobDetailHeader({ job, onEditIdentity }: JobDetailHeaderProps) {
  const { retry, cancel, pause, resume, deleteJob } = useJobActions(job.id);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const canRetry = job.status === "failed" || job.status === "cancelled";
  const canCancel = job.status === "queued" || job.status === "running" || job.status === "paused";
  const canPause = job.status === "running";
  const canResume = job.status === "paused";

  const failedIndex = job.tasks.findIndex((t) => t.status === "failed");
  const retryFromOptions =
    failedIndex === -1 ? TASK_KIND_ORDER : TASK_KIND_ORDER.slice(failedIndex);

  return (
    <div className="flex flex-col gap-4 sm:flex-row">
      <PosterImage
        src={job.poster_url}
        alt={jobDisplayTitle(job.title, job.year)}
        className="h-36 w-24 shrink-0 rounded-lg"
      />
      <div className="flex-1 space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <button
              type="button"
              onClick={onEditIdentity}
              className="hover:text-primary text-left text-2xl font-semibold transition-colors"
            >
              {jobDisplayTitle(job.title, job.year)}
            </button>
            <div className="flex items-center gap-2">
              <StatusBadge status={job.status} />
              {job.confidence !== null && (
                <span className="text-muted-foreground text-xs">
                  {Math.round(job.confidence * 100)}% confidence
                </span>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {canRetry && (
              <Button size="sm" disabled={retry.isPending} onClick={() => retry.mutate(undefined)}>
                <RotateCcw />
                Retry
              </Button>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="outline">
                  Retry from…
                  <ChevronDown />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {retryFromOptions.map((kind, i) => (
                  <DropdownMenuItem
                    key={kind}
                    disabled={retry.isPending}
                    onClick={() => retry.mutate({ from_stage: kind })}
                  >
                    <div>
                      <p>{TASK_KIND_LABELS[kind]}</p>
                      <p className="text-muted-foreground text-xs">
                        Redoes{" "}
                        {retryFromOptions
                          .slice(i)
                          .map((k) => TASK_KIND_LABELS[k])
                          .join(", ")}
                        .
                      </p>
                    </div>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
            {canPause && (
              <Button
                size="sm"
                variant="outline"
                disabled={pause.isPending}
                onClick={() => pause.mutate()}
              >
                <Pause />
                Pause
              </Button>
            )}
            {canResume && (
              <Button
                size="sm"
                variant="outline"
                disabled={resume.isPending}
                onClick={() => resume.mutate()}
              >
                <Play />
                Resume
              </Button>
            )}
            {canCancel && (
              <Button
                size="sm"
                variant="outline"
                disabled={cancel.isPending}
                onClick={() => cancel.mutate()}
              >
                <X />
                Cancel
              </Button>
            )}
            <Button size="sm" variant="outline" onClick={() => setConfirmOpen(true)}>
              <Trash2 />
              Delete
            </Button>
          </div>
        </div>

        <StageProgress
          jobStatus={job.status}
          currentTask={job.current_task}
          tasks={job.tasks}
          size="md"
        />

        {job.error && (
          <p className="border-status-danger/40 bg-status-danger/5 text-status-danger rounded-md border p-2 text-sm">
            {job.error}
          </p>
        )}
      </div>

      <JobDeleteDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        jobTitle={jobDisplayTitle(job.title, job.year)}
        deleteJob={deleteJob}
      />
    </div>
  );
}
