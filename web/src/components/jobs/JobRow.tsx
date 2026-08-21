import { Link } from "@tanstack/react-router";

import { JobActionsMenu } from "@/components/jobs/JobActionsMenu";
import { PosterImage } from "@/components/common/PosterImage";
import { StageProgress } from "@/components/status/StageProgress";
import { StatusBadge } from "@/components/status/StatusBadge";
import { Button } from "@/components/ui/button";
import type { JobSummary } from "@/lib/api";
import { formatEta, jobDisplayTitle } from "@/lib/format";
import { useJobProgress } from "@/providers/EventProvider";
import { cn } from "@/lib/utils";

interface JobRowProps {
  job: JobSummary;
  onReview?: (job: JobSummary) => void;
}

export function JobRow({ job, onReview }: JobRowProps) {
  const live = useJobProgress(job.id);
  const currentTask = live?.taskKind ?? job.current_task;
  const eta = live?.etaSeconds ?? job.eta_seconds;
  const needsReview = job.status === "needs_review";

  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-lg border p-3",
        needsReview ? "border-status-warning/50 border-l-4 bg-status-warning/5" : "bg-card",
      )}
    >
      <PosterImage
        src={job.poster_url}
        alt={jobDisplayTitle(job.title, job.year)}
        className="h-14 w-10 shrink-0 rounded-md"
      />
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex items-center gap-2">
          <Link
            to="/jobs/$jobId"
            params={{ jobId: String(job.id) }}
            className="hover:text-primary truncate text-sm font-medium transition-colors"
          >
            {jobDisplayTitle(job.title, job.year)}
          </Link>
          <StatusBadge status={job.status} size="sm" />
        </div>
        <StageProgress jobStatus={job.status} currentTask={currentTask} size="sm" />
      </div>
      <div className="text-muted-foreground hidden shrink-0 text-xs sm:block">
        {eta !== null ? `ETA ${formatEta(eta)}` : null}
      </div>
      {needsReview && onReview ? (
        <Button size="sm" variant="outline" onClick={() => onReview(job)}>
          Review
        </Button>
      ) : (
        <JobActionsMenu job={job} />
      )}
    </div>
  );
}
