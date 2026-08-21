import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { Download, ScrollText } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/common/EmptyState";
import { StatusBadge } from "@/components/status/StatusBadge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { type EventOut, api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import { usePageTitle } from "@/providers/PageTitleProvider";

type Level = "all" | "error" | "warning" | "info";

function eventLevel(event: EventOut): Exclude<Level, "all"> {
  if (event.type === "system.warning") return "warning";
  if (event.type === "task.failed") return "error";
  if (event.type === "job.status_changed") {
    const to = (event.data as { to?: string }).to;
    if (to === "failed") return "error";
    if (to === "needs_review") return "warning";
  }
  return "info";
}

const LEVEL_STATUS = {
  error: "failed",
  warning: "needs_review",
  info: "succeeded",
} as const;

export const Route = createFileRoute("/logs/")({
  component: LogsPage,
});

function LogsPage() {
  usePageTitle("Logs");
  const [level, setLevel] = useState<Level>("all");

  const { data, isPending } = useQuery({
    queryKey: queryKeys.events.list(),
    queryFn: () => api.events.list({ limit: 200 }),
    refetchInterval: 5000,
  });

  const events = data?.items ?? [];
  const filtered = level === "all" ? events : events.filter((e) => eventLevel(e) === level);

  function download() {
    const text = filtered.map((e) => `${e.ts}\t${e.type}\t${JSON.stringify(e.data)}`).join("\n");
    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `spindle-events-${new Date().toISOString().slice(0, 19)}.log`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <Select value={level} onValueChange={(v) => setLevel(v as Level)}>
          <SelectTrigger size="sm" className="w-32">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All levels</SelectItem>
            <SelectItem value="error">Error</SelectItem>
            <SelectItem value="warning">Warning</SelectItem>
            <SelectItem value="info">Info</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" onClick={download} disabled={filtered.length === 0}>
          <Download />
          Download
        </Button>
      </div>

      {!isPending && filtered.length === 0 && (
        <EmptyState icon={ScrollText} title="No events at this level yet." />
      )}

      {filtered.length > 0 && (
        <div className="divide-border divide-y rounded-lg border">
          {filtered.map((event) => (
            <div key={event.id} className="flex items-center gap-3 px-3 py-2 text-sm">
              <span className="text-muted-foreground w-40 shrink-0 font-mono text-xs tabular-nums">
                {new Date(event.ts).toLocaleString()}
              </span>
              <StatusBadge status={LEVEL_STATUS[eventLevel(event)]} hideLabel size="sm" />
              <span className="font-mono text-xs font-medium">{event.type}</span>
              {Object.keys(event.data).length > 0 && (
                <span className="text-muted-foreground truncate font-mono text-xs">
                  {JSON.stringify(event.data)}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
