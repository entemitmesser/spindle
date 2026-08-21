import { useQueryClient } from "@tanstack/react-query";
import {
  type PropsWithChildren,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

import type { TaskKind } from "@/lib/api";
import { INVALIDATIONS } from "@/lib/query-keys";
import { type SpindleEvent, subscribeToEvents } from "@/lib/sse";

const MIN_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30000;
const MAX_DEDUPE_ENTRIES = 500;

type JobListener = (event: SpindleEvent) => void;

interface EventStreamContextValue {
  connected: boolean;
  subscribeJob: (jobId: number, listener: JobListener) => () => void;
}

const EventStreamContext = createContext<EventStreamContextValue | null>(null);

function jobTitleFromCache(queryClient: ReturnType<typeof useQueryClient>, jobId: number): string {
  const detail = queryClient.getQueryData<{ title: string | null; year: number | null }>([
    "jobs",
    "detail",
    jobId,
  ]);
  if (detail?.title) {
    return detail.year ? `${detail.title} (${detail.year})` : detail.title;
  }
  return `Job #${jobId}`;
}

function handleToast(
  event: SpindleEvent,
  queryClient: ReturnType<typeof useQueryClient>,
  seen: Set<string>,
): void {
  if (event.type === "job.status_changed") {
    const to = (event.data as { to?: string }).to;
    if (to !== "completed" && to !== "failed" && to !== "needs_review") return;
    if (event.job_id === null) return;

    const key = `${event.job_id}:${to}`;
    if (seen.has(key)) return;
    seen.add(key);
    if (seen.size > MAX_DEDUPE_ENTRIES) {
      const oldest = seen.values().next().value;
      if (oldest !== undefined) seen.delete(oldest);
    }

    const name = jobTitleFromCache(queryClient, event.job_id);
    if (to === "completed") toast.success(`${name} completed`);
    else if (to === "failed") toast.error(`${name} failed`);
    else toast.warning(`${name} needs review`);
    return;
  }

  if (event.type === "system.warning") {
    const data = event.data as { code?: string; message?: string };
    const key = `warning:${data.code}:${data.message}`;
    if (seen.has(key)) return;
    seen.add(key);
    toast.warning(data.message ?? "System warning", { description: data.code });
  }
}

export function EventProvider({ children }: PropsWithChildren) {
  const queryClient = useQueryClient();
  // Optimistic: EventSource only fires "open" once the first byte of body
  // arrives, and the server may go quiet for up to its 20s heartbeat when
  // there's no replay backlog (architecture.md §4) — waiting for "open"
  // would show "Reconnecting…" on a perfectly healthy, just-quiet stream.
  // A real failure fires "error" promptly, so that's the only signal that
  // should flip this to false.
  const [connected, setConnected] = useState(true);
  const jobListenersRef = useRef(new Map<number, Set<JobListener>>());
  const toastedRef = useRef(new Set<string>());

  const subscribeJob = useCallback((jobId: number, listener: JobListener) => {
    const listeners = jobListenersRef.current;
    let set = listeners.get(jobId);
    if (!set) {
      set = new Set();
      listeners.set(jobId, set);
    }
    set.add(listener);
    return () => {
      set?.delete(listener);
      if (set && set.size === 0) listeners.delete(jobId);
    };
  }, []);

  useEffect(() => {
    let stopped = false;
    let unsubscribe: (() => void) | null = null;
    let backoff = MIN_BACKOFF_MS;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    function handleEvent(event: SpindleEvent) {
      if (event.job_id !== null) {
        jobListenersRef.current.get(event.job_id)?.forEach((listener) => listener(event));
      }
      for (const key of INVALIDATIONS[event.type](event)) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
      handleToast(event, queryClient, toastedRef.current);
    }

    function open() {
      unsubscribe = subscribeToEvents(handleEvent, {
        onOpen: () => {
          backoff = MIN_BACKOFF_MS;
          setConnected(true);
        },
        onError: () => {
          setConnected(false);
          unsubscribe?.();
          unsubscribe = null;
          if (stopped) return;
          reconnectTimer = setTimeout(open, backoff);
          backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
        },
      });
    }

    open();

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      unsubscribe?.();
    };
  }, [queryClient]);

  return (
    <EventStreamContext.Provider value={{ connected, subscribeJob }}>
      {children}
    </EventStreamContext.Provider>
  );
}

export function useEventStream(): EventStreamContextValue {
  const ctx = useContext(EventStreamContext);
  if (!ctx) throw new Error("useEventStream must be used within EventProvider");
  return ctx;
}

export interface LiveJobProgress {
  taskKind: TaskKind;
  progress: number;
  etaSeconds: number | null;
  message: string | null;
  speed: string | null;
}

interface TaskProgressData {
  kind: TaskKind;
  progress: number;
  eta_seconds: number | null;
  speed: string | null;
  message: string | null;
}

/**
 * High-frequency per-job progress, fed straight from SSE `task.progress`
 * events — never through query invalidation (see query-keys.ts). Resets to
 * `null` on a stage transition so callers fall back to the REST-fetched
 * `JobSummary`/`JobDetail` value until the next tick arrives.
 */
export function useJobProgress(jobId: number): LiveJobProgress | null {
  const { subscribeJob } = useEventStream();
  const [live, setLive] = useState<LiveJobProgress | null>(null);
  const [trackedJobId, setTrackedJobId] = useState(jobId);

  // Reset synchronously during render on a job change (React's "adjusting
  // state when a prop changes" pattern) instead of in the effect below, so a
  // stale progress value never flashes for the new job.
  if (jobId !== trackedJobId) {
    setTrackedJobId(jobId);
    setLive(null);
  }

  useEffect(() => {
    return subscribeJob(jobId, (event) => {
      if (event.type === "task.progress") {
        const data = event.data as unknown as TaskProgressData;
        setLive({
          taskKind: data.kind,
          progress: data.progress,
          etaSeconds: data.eta_seconds ?? null,
          message: data.message ?? null,
          speed: data.speed ?? null,
        });
      } else {
        setLive(null);
      }
    });
  }, [jobId, subscribeJob]);

  return live;
}
