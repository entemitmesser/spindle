import type { JobStatus, TaskKind, TaskOut, TaskStatus } from "@/lib/api";
import { TASK_KIND_LABELS, TASK_KIND_ORDER, deriveStageStatuses } from "@/lib/stage-progress";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

const SEGMENT_CLASSES: Record<TaskStatus, string> = {
  succeeded: "bg-status-success",
  running: "bg-status-active stage-segment-active",
  pending: "bg-muted",
  failed: "bg-status-danger",
  skipped: "bg-muted opacity-50",
};

function segmentLabel(kind: TaskKind, status: TaskStatus, task?: TaskOut): string {
  const base = `${TASK_KIND_LABELS[kind]}: ${status}`;
  if (task?.error) return `${base} — ${task.error}`;
  if (task?.message) return `${base} — ${task.message}`;
  return base;
}

interface StageProgressProps {
  jobStatus: JobStatus;
  currentTask: TaskKind | null;
  tasks?: TaskOut[];
  size?: "sm" | "md";
  className?: string;
}

export function StageProgress({
  jobStatus,
  currentTask,
  tasks,
  size = "sm",
  className,
}: StageProgressProps) {
  const statuses = deriveStageStatuses(jobStatus, currentTask, tasks);
  const tasksByKind = new Map(tasks?.map((t) => [t.kind, t]));

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <div className={cn("flex gap-1", size === "sm" ? "h-1.5" : "h-2")}>
        {TASK_KIND_ORDER.map((kind) => {
          const status = statuses[kind];
          const task = tasksByKind.get(kind);
          return (
            <Tooltip key={kind}>
              <TooltipTrigger asChild>
                <div
                  className={cn(
                    "flex-1 rounded-full transition-colors duration-150",
                    SEGMENT_CLASSES[status],
                  )}
                  aria-label={segmentLabel(kind, status, task)}
                />
              </TooltipTrigger>
              <TooltipContent>{segmentLabel(kind, status, task)}</TooltipContent>
            </Tooltip>
          );
        })}
      </div>
      {size === "md" && (
        <div className="text-muted-foreground grid grid-cols-6 gap-1 text-center">
          {TASK_KIND_ORDER.map((kind) => (
            <span key={kind} className="truncate text-[11px]">
              {TASK_KIND_LABELS[kind]}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
