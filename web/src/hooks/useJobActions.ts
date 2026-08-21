import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";

import { ApiError, type JobRetryRequest, api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong.";
}

function invalidateJob(queryClient: ReturnType<typeof useQueryClient>, jobId: number) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.detail(jobId) });
  void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() });
  void queryClient.invalidateQueries({ queryKey: queryKeys.queue.list() });
}

/** Retry/cancel/pause/resume/delete mutations for a job, shared between
 * `JobActionsMenu` (rows) and the job detail header's action buttons. */
export function useJobActions(jobId: number) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const retry = useMutation({
    mutationFn: (body?: JobRetryRequest) => api.jobs.retry(jobId, body),
    onSuccess: () => {
      toast.success("Job queued for retry.");
      invalidateJob(queryClient, jobId);
    },
    onError: (error) => toast.error(`Retry failed: ${errorMessage(error)}`),
  });

  const cancel = useMutation({
    mutationFn: () => api.jobs.cancel(jobId),
    onSuccess: () => {
      toast.success("Job cancelled.");
      invalidateJob(queryClient, jobId);
    },
    onError: (error) => toast.error(`Cancel failed: ${errorMessage(error)}`),
  });

  const pause = useMutation({
    mutationFn: () => api.jobs.pause(jobId),
    onSuccess: () => {
      toast.success("Job paused.");
      invalidateJob(queryClient, jobId);
    },
    onError: (error) => toast.error(`Pause failed: ${errorMessage(error)}`),
  });

  const resume = useMutation({
    mutationFn: () => api.jobs.resume(jobId),
    onSuccess: () => {
      toast.success("Job resumed.");
      invalidateJob(queryClient, jobId);
    },
    onError: (error) => toast.error(`Resume failed: ${errorMessage(error)}`),
  });

  const deleteJob = useMutation({
    mutationFn: (purgeFiles: boolean) => api.jobs.delete(jobId, purgeFiles),
    onSuccess: () => {
      toast.success("Job deleted.");
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.queue.list() });
      void navigate({ to: "/jobs" });
    },
    onError: (error) => toast.error(`Delete failed: ${errorMessage(error)}`),
  });

  return { retry, cancel, pause, resume, deleteJob };
}
