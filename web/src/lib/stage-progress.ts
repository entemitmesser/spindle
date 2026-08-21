import type { JobStatus, TaskKind, TaskOut, TaskStatus } from "@/lib/api";

/** Pipeline stage order — mirrors the `TaskKind` enum declaration order the
 * dispatcher iterates (architecture.md §1). Never hardcode this elsewhere. */
export const TASK_KIND_ORDER: TaskKind[] = [
  "identify",
  "rip",
  "transcode",
  "subtitles",
  "organize",
  "notify",
];

export const TASK_KIND_LABELS: Record<TaskKind, string> = {
  identify: "Identify",
  rip: "Rip",
  transcode: "Transcode",
  subtitles: "Subtitles",
  organize: "Organize",
  notify: "Notify",
};

/**
 * Six-segment stage statuses for `StageProgress`. `JobDetail.tasks` is
 * authoritative when available; `JobSummary` (job rows, drive cards) only
 * carries `current_task` + job `status`, so this derives a reasonable
 * approximation from those instead.
 */
export function deriveStageStatuses(
  jobStatus: JobStatus,
  currentTask: TaskKind | null,
  tasks?: TaskOut[],
): Record<TaskKind, TaskStatus> {
  if (tasks && tasks.length > 0) {
    const byKind = new Map(tasks.map((t) => [t.kind, t.status]));
    return Object.fromEntries(
      TASK_KIND_ORDER.map((kind) => [kind, byKind.get(kind) ?? "pending"]),
    ) as Record<TaskKind, TaskStatus>;
  }

  if (jobStatus === "completed") {
    return Object.fromEntries(TASK_KIND_ORDER.map((kind) => [kind, "succeeded"])) as Record<
      TaskKind,
      TaskStatus
    >;
  }

  const currentIndex = currentTask ? TASK_KIND_ORDER.indexOf(currentTask) : -1;
  return Object.fromEntries(
    TASK_KIND_ORDER.map((kind, index) => {
      if (currentIndex === -1 || index > currentIndex) return [kind, "pending"];
      if (index < currentIndex) return [kind, "succeeded"];
      // index === currentIndex
      if (jobStatus === "failed" || jobStatus === "needs_review") return [kind, "failed"];
      if (jobStatus === "running") return [kind, "running"];
      return [kind, "pending"];
    }),
  ) as Record<TaskKind, TaskStatus>;
}
