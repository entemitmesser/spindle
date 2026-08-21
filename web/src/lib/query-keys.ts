import type { QueryKey } from "@tanstack/react-query";

import type { EventType, SpindleEvent } from "@/lib/sse";

export const queryKeys = {
  auth: {
    me: () => ["auth", "me"] as const,
  },
  system: {
    health: () => ["system", "health"] as const,
    info: () => ["system", "info"] as const,
  },
  drives: {
    list: () => ["drives", "list"] as const,
  },
  jobs: {
    list: (filters?: unknown) => ["jobs", "list", filters] as const,
    detail: (jobId: number) => ["jobs", "detail", jobId] as const,
  },
  queue: {
    list: () => ["queue", "list"] as const,
  },
  settings: {
    get: () => ["settings"] as const,
    presets: () => ["settings", "presets"] as const,
  },
  events: {
    list: (jobId?: number) => ["events", "list", jobId] as const,
  },
} as const;

/**
 * One place, so the UI never gets stale for a reason nobody can find
 * (phase-07-api-realtime.md task 6). `web/src/lib/sse.ts`'s subscriber
 * calls this to turn an incoming `SpindleEvent` into the query keys to
 * invalidate.
 */
export const INVALIDATIONS: Record<EventType, (event: SpindleEvent) => QueryKey[]> = {
  "drive.disc_inserted": () => [queryKeys.drives.list()],
  "drive.disc_ejected": () => [queryKeys.drives.list()],
  "drive.status_changed": () => [queryKeys.drives.list()],

  "job.created": () => [queryKeys.jobs.list(), queryKeys.queue.list()],
  "job.updated": (event) => [
    ...(event.job_id !== null ? [queryKeys.jobs.detail(event.job_id)] : []),
    queryKeys.jobs.list(),
  ],
  "job.status_changed": (event) => [
    ...(event.job_id !== null ? [queryKeys.jobs.detail(event.job_id)] : []),
    queryKeys.jobs.list(),
    queryKeys.queue.list(),
    queryKeys.drives.list(),
  ],

  "task.started": (event) => [
    ...(event.job_id !== null ? [queryKeys.jobs.detail(event.job_id)] : []),
    queryKeys.jobs.list(),
  ],
  // Progress ticks never invalidate — they'd hammer the API. `useJobProgress`
  // (providers/EventProvider.tsx) feeds progress bars from local state instead;
  // this event only reaches components through that side channel.
  "task.progress": () => [],
  "task.succeeded": (event) => [
    ...(event.job_id !== null ? [queryKeys.jobs.detail(event.job_id)] : []),
    queryKeys.jobs.list(),
    queryKeys.queue.list(),
  ],
  "task.failed": (event) => [
    ...(event.job_id !== null ? [queryKeys.jobs.detail(event.job_id)] : []),
    queryKeys.jobs.list(),
    queryKeys.queue.list(),
  ],

  "settings.updated": () => [queryKeys.settings.get()],
  "system.warning": () => [queryKeys.system.info()],
};
