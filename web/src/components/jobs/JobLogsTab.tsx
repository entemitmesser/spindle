import { useState } from "react";

import { LogViewer } from "@/components/common/LogViewer";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useJobLog } from "@/hooks/useJobLog";
import type { JobDetail, TaskKind } from "@/lib/api";
import { TASK_KIND_LABELS, TASK_KIND_ORDER } from "@/lib/stage-progress";

export function JobLogsTab({ job }: { job: JobDetail }) {
  const startedKinds = new Set(job.tasks.filter((t) => t.attempts > 0).map((t) => t.kind));
  const availableKinds = TASK_KIND_ORDER.filter((k) => startedKinds.has(k));
  const [kind, setKind] = useState<TaskKind>(availableKinds[0] ?? "identify");

  const { data: lines = [], isPending } = useJobLog(job.id, kind, true);

  return (
    <div className="space-y-3">
      <Select value={kind} onValueChange={(v) => setKind(v as TaskKind)}>
        <SelectTrigger size="sm" className="w-40">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {TASK_KIND_ORDER.map((k) => (
            <SelectItem key={k} value={k} disabled={!startedKinds.has(k)}>
              {TASK_KIND_LABELS[k]}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <LogViewer
        lines={lines}
        emptyMessage={
          isPending ? "Loading…" : `No ${TASK_KIND_LABELS[kind].toLowerCase()} log yet.`
        }
      />
    </div>
  );
}
