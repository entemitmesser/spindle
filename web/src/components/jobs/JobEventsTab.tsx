import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";

import { EmptyState } from "@/components/common/EmptyState";
import { api } from "@/lib/api";

export function JobEventsTab({ jobId }: { jobId: number }) {
  const { data } = useQuery({
    queryKey: ["events", "list", jobId],
    queryFn: () => api.events.list({ job_id: jobId, limit: 200 }),
  });

  const events = data?.items ?? [];
  if (events.length === 0) {
    return (
      <EmptyState icon={History} title="No events recorded yet." className="border-none p-4" />
    );
  }

  return (
    <ol className="space-y-2">
      {events.map((event) => (
        <li key={event.id} className="flex items-start gap-3 text-sm">
          <span className="text-muted-foreground w-40 shrink-0 font-mono text-xs tabular-nums">
            {new Date(event.ts).toLocaleString()}
          </span>
          <span className="font-mono text-xs font-medium">{event.type}</span>
          {Object.keys(event.data).length > 0 && (
            <span className="text-muted-foreground truncate font-mono text-xs">
              {JSON.stringify(event.data)}
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}
