import type { components } from "@/lib/api-types";

export type HealthResponse = components["schemas"]["HealthResponse"];
export type SystemInfo = components["schemas"]["SystemInfo"];

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, init);

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ErrorBody | null;
    throw new ApiError(
      response.status,
      body?.error ?? "unknown_error",
      body?.message ?? response.statusText,
      body?.detail,
    );
  }

  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthResponse>("/system/health"),
  system: () => request<SystemInfo>("/system"),
};
