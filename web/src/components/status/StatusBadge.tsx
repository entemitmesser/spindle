import type { DriveStatus, JobStatus, TaskStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

export type AnyStatus = JobStatus | TaskStatus | DriveStatus;

type StatusKind = "idle" | "active" | "success" | "warning" | "danger";

interface StatusMeta {
  kind: StatusKind;
  label: string;
}

/**
 * Single source of status colour truth (ui-design.md §4) — every status enum
 * (`JobStatus`, `TaskStatus`, `DriveStatus`) maps here. No overlapping string
 * ever means something different across enums, so one flat map is safe.
 */
const STATUS_META: Record<AnyStatus, StatusMeta> = {
  // JobStatus
  queued: { kind: "idle", label: "Queued" },
  running: { kind: "active", label: "Running" },
  paused: { kind: "idle", label: "Paused" },
  needs_review: { kind: "warning", label: "Needs review" },
  completed: { kind: "success", label: "Completed" },
  failed: { kind: "danger", label: "Failed" },
  cancelled: { kind: "idle", label: "Cancelled" },
  // TaskStatus
  pending: { kind: "idle", label: "Pending" },
  succeeded: { kind: "success", label: "Succeeded" },
  skipped: { kind: "idle", label: "Skipped" },
  // DriveStatus
  empty: { kind: "idle", label: "Empty" },
  loading: { kind: "active", label: "Loading" },
  ready: { kind: "success", label: "Ready" },
  busy: { kind: "active", label: "Busy" },
  error: { kind: "danger", label: "Error" },
  disabled: { kind: "idle", label: "Disabled" },
};

const DOT_CLASSES: Record<StatusKind, string> = {
  idle: "bg-status-idle",
  active: "bg-status-active",
  success: "bg-status-success",
  warning: "bg-status-warning",
  danger: "bg-status-danger",
};

const TEXT_CLASSES: Record<StatusKind, string> = {
  idle: "text-muted-foreground",
  active: "text-status-active",
  success: "text-status-success",
  warning: "text-status-warning",
  danger: "text-status-danger",
};

interface StatusBadgeProps {
  status: AnyStatus;
  /** Override the default label (e.g. to append context like "Retrying…"). */
  label?: string;
  size?: "sm" | "default";
  /** Visually hide the label but keep it for screen readers. Colour is never
   * the only signal, so the dot alone is not a substitute for this. */
  hideLabel?: boolean;
  className?: string;
}

export function StatusBadge({
  status,
  label,
  size = "default",
  hideLabel = false,
  className,
}: StatusBadgeProps) {
  const meta = STATUS_META[status];
  const isActive = meta.kind === "active";

  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <span
        className={cn(
          "shrink-0 rounded-full",
          size === "sm" ? "size-1.5" : "size-2",
          DOT_CLASSES[meta.kind],
          isActive && "animate-pulse motion-reduce:animate-none",
        )}
        aria-hidden="true"
      />
      <span
        className={cn(
          size === "sm" ? "text-xs" : "text-sm",
          TEXT_CLASSES[meta.kind],
          hideLabel && "sr-only",
        )}
      >
        {label ?? meta.label}
      </span>
    </span>
  );
}
