import { EllipsisVertical, Pause, Play, RotateCcw, Trash2, X } from "lucide-react";
import { useState } from "react";

import { JobDeleteDialog } from "@/components/jobs/JobDeleteDialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useJobActions } from "@/hooks/useJobActions";
import type { JobSummary } from "@/lib/api";
import { jobDisplayTitle } from "@/lib/format";

export function JobActionsMenu({ job }: { job: JobSummary }) {
  const { retry, cancel, pause, resume, deleteJob } = useJobActions(job.id);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const canRetry = job.status === "failed" || job.status === "cancelled";
  const canCancel = job.status === "queued" || job.status === "running" || job.status === "paused";
  const canPause = job.status === "running";
  const canResume = job.status === "paused";

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label="Job actions">
            <EllipsisVertical className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {canRetry && (
            <DropdownMenuItem disabled={retry.isPending} onClick={() => retry.mutate(undefined)}>
              <RotateCcw />
              Retry
            </DropdownMenuItem>
          )}
          {canPause && (
            <DropdownMenuItem disabled={pause.isPending} onClick={() => pause.mutate()}>
              <Pause />
              Pause
            </DropdownMenuItem>
          )}
          {canResume && (
            <DropdownMenuItem disabled={resume.isPending} onClick={() => resume.mutate()}>
              <Play />
              Resume
            </DropdownMenuItem>
          )}
          {canCancel && (
            <DropdownMenuItem disabled={cancel.isPending} onClick={() => cancel.mutate()}>
              <X />
              Cancel
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onClick={() => setConfirmOpen(true)}>
            <Trash2 />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <JobDeleteDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        jobTitle={jobDisplayTitle(job.title, job.year)}
        deleteJob={deleteJob}
      />
    </>
  );
}
