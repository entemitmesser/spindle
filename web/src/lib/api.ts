import type { components, operations } from "@/lib/api-types";

export type HealthResponse = components["schemas"]["HealthResponse"];
export type SystemInfo = components["schemas"]["SystemInfo"];
export type JobSummary = components["schemas"]["JobSummary"];
export type JobDetail = components["schemas"]["JobDetail"];
export type JobCreate = components["schemas"]["JobCreate"];
export type JobPatch = components["schemas"]["JobPatch"];
export type JobRetryRequest = components["schemas"]["JobRetryRequest"];
export type JobStatus = components["schemas"]["JobStatus"];
export type TaskKind = components["schemas"]["TaskKind"];
export type MediaKind = components["schemas"]["MediaKind"];
export type DriveOut = components["schemas"]["DriveOut"];
export type DrivePatch = components["schemas"]["DrivePatch"];
export type DrivesResponse = components["schemas"]["DrivesResponse"];
export type QueueResponse = components["schemas"]["QueueResponse"];
export type SettingsResponse = components["schemas"]["RuntimeConfig"];
export type PresetOut = components["schemas"]["PresetOut"];
export type SettingsTestResult = components["schemas"]["SettingsTestResult"];
export type SettingsTestTarget = "tmdb" | "opensubtitles" | "jellyfin" | "notify";
export type MetadataSearchResponse = components["schemas"]["MetadataSearchResponse"];
export type MetadataDetail = components["schemas"]["MetadataDetail"];
export type EventOut = components["schemas"]["EventOut"];

// `Page_JobSummary_`/`Page_EventOut_` are openapi-typescript's mangled names
// for FastAPI's generic `Page[JobSummary]`/`Page[EventOut]` — this local
// alias is the shape both actually have, so callers don't need to know that.
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: unknown;

  constructor(status: number, code: string, message: string, detail: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

interface ErrorBody {
  error: string;
  message: string;
  detail: unknown;
}

type QueryValue = string | number | boolean | null | undefined;
type QueryParams = Record<string, QueryValue | QueryValue[]>;

function buildQuery(params?: QueryParams): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    const values = Array.isArray(value) ? value : [value];
    for (const item of values) {
      if (item !== undefined && item !== null) search.append(key, String(item));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

interface RequestOptions {
  method?: string;
  query?: QueryParams;
  body?: unknown;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", query, body } = options;
  const response = await fetch(`/api/v1${path}${buildQuery(query)}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    const errorBody = (await response.json().catch(() => null)) as ErrorBody | null;
    throw new ApiError(
      response.status,
      errorBody?.error ?? "unknown_error",
      errorBody?.message ?? response.statusText,
      errorBody?.detail,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

type ListJobsQuery = NonNullable<operations["list_jobs"]["parameters"]["query"]>;
type ListEventsQuery = NonNullable<operations["list_events"]["parameters"]["query"]>;

export const api = {
  health: () => request<HealthResponse>("/system/health"),
  system: () => request<SystemInfo>("/system"),

  drives: {
    list: () => request<DrivesResponse>("/drives"),
    eject: (driveId: number) =>
      request<{ ok: boolean }>(`/drives/${driveId}/eject`, { method: "POST" }),
    rescan: (driveId: number) => request<DriveOut>(`/drives/${driveId}/rescan`, { method: "POST" }),
    update: (driveId: number, body: DrivePatch) =>
      request<DriveOut>(`/drives/${driveId}`, { method: "PATCH", body }),
  },

  jobs: {
    list: (query?: ListJobsQuery) => request<Page<JobSummary>>("/jobs", { query }),
    create: (body: JobCreate) => request<JobDetail>("/jobs", { method: "POST", body }),
    get: (jobId: number) => request<JobDetail>(`/jobs/${jobId}`),
    update: (jobId: number, body: JobPatch) =>
      request<JobDetail>(`/jobs/${jobId}`, { method: "PATCH", body }),
    retry: (jobId: number, body?: JobRetryRequest) =>
      request<JobDetail>(`/jobs/${jobId}/retry`, { method: "POST", body }),
    cancel: (jobId: number) => request<JobDetail>(`/jobs/${jobId}/cancel`, { method: "POST" }),
    pause: (jobId: number) => request<JobDetail>(`/jobs/${jobId}/pause`, { method: "POST" }),
    resume: (jobId: number) => request<JobDetail>(`/jobs/${jobId}/resume`, { method: "POST" }),
    delete: (jobId: number, purgeFiles = false) =>
      request<void>(`/jobs/${jobId}`, { method: "DELETE", query: { purge_files: purgeFiles } }),
    logUrl: (jobId: number, kind: TaskKind, tail?: number) =>
      `/api/v1/jobs/${jobId}/logs/${kind}${buildQuery(tail ? { tail } : undefined)}`,
  },

  queue: {
    get: () => request<QueueResponse>("/queue"),
    reorder: (jobIds: number[]) =>
      request<QueueResponse>("/queue/reorder", { method: "POST", body: { job_ids: jobIds } }),
  },

  settings: {
    get: () => request<SettingsResponse>("/settings"),
    update: (partial: Record<string, unknown>) =>
      request<SettingsResponse>("/settings", { method: "PUT", body: partial }),
    presets: () => request<PresetOut[]>("/settings/presets"),
    test: (target: SettingsTestTarget) =>
      request<SettingsTestResult>(`/settings/test/${target}`, { method: "POST" }),
  },

  metadata: {
    search: (q: string, mediaKind?: MediaKind) =>
      request<MetadataSearchResponse>("/metadata/search", { query: { q, media_kind: mediaKind } }),
    get: (tmdbId: number, mediaKind?: MediaKind) =>
      request<MetadataDetail>(`/metadata/${tmdbId}`, { query: { media_kind: mediaKind } }),
  },

  events: {
    list: (query?: ListEventsQuery) => request<Page<EventOut>>("/events", { query }),
    streamUrl: "/api/v1/events/stream",
  },
};
