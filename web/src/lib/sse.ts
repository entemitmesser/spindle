/**
 * `GET /api/v1/events/stream` client, per architecture.md §5 and CLAUDE.md's
 * "Realtime" row: the UI treats events as cache invalidations for TanStack
 * Query plus optimistic progress updates — it never rebuilds entities from
 * the stream alone.
 *
 * Native `EventSource` sends `Last-Event-ID` automatically on reconnect
 * (part of the spec), so resuming after a dropped connection is free — no
 * manual bookkeeping here.
 */

export type EventType =
  | "drive.disc_inserted"
  | "drive.disc_ejected"
  | "drive.status_changed"
  | "job.created"
  | "job.updated"
  | "job.status_changed"
  | "task.started"
  | "task.progress"
  | "task.succeeded"
  | "task.failed"
  | "settings.updated"
  | "system.warning";

export interface SpindleEvent<TData = Record<string, unknown>> {
  type: EventType;
  ts: string;
  job_id: number | null;
  drive_id: number | null;
  data: TData;
}

const EVENT_TYPES: EventType[] = [
  "drive.disc_inserted",
  "drive.disc_ejected",
  "drive.status_changed",
  "job.created",
  "job.updated",
  "job.status_changed",
  "task.started",
  "task.progress",
  "task.succeeded",
  "task.failed",
  "settings.updated",
  "system.warning",
];

export interface SubscribeOptions {
  /** Fired when the connection (re)opens. */
  onOpen?: () => void;
  /** Fired on a connection error. The native EventSource retries on its own,
   * but callers that want custom backoff should close() via the returned
   * unsubscribe and open a fresh subscription themselves. */
  onError?: () => void;
}

/** Subscribes to every known event type and returns an unsubscribe function. */
export function subscribeToEvents(
  onEvent: (event: SpindleEvent) => void,
  options: SubscribeOptions = {},
): () => void {
  const source = new EventSource("/api/v1/events/stream");

  if (options.onOpen) source.addEventListener("open", options.onOpen);
  if (options.onError) source.addEventListener("error", options.onError);

  const handlers = EVENT_TYPES.map((type) => {
    const handler = (message: MessageEvent<string>) => {
      try {
        onEvent(JSON.parse(message.data) as SpindleEvent);
      } catch {
        // Malformed payload — drop it rather than crash the subscription.
      }
    };
    source.addEventListener(type, handler);
    return { type, handler };
  });

  return () => {
    for (const { type, handler } of handlers) {
      source.removeEventListener(type, handler);
    }
    source.close();
  };
}
