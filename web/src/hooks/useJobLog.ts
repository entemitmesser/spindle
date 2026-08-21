import { useQuery } from "@tanstack/react-query";

import { type TaskKind, api } from "@/lib/api";

/** Polls a job's per-stage log file as plain text (`GET /jobs/{id}/logs/{kind}`)
 * and splits it into lines for `LogViewer`. There's no streaming/Range
 * support on the client yet, so "follow" is a poll, not a true tail -f. */
export function useJobLog(jobId: number, kind: TaskKind, enabled: boolean) {
  return useQuery({
    queryKey: ["jobs", "log", jobId, kind],
    queryFn: async () => {
      const response = await fetch(api.jobs.logUrl(jobId, kind));
      if (!response.ok) return [];
      const text = await response.text();
      return text.length > 0 ? text.split("\n") : [];
    },
    enabled,
    refetchInterval: 3000,
  });
}
