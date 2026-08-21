import { StatusBadge } from "@/components/status/StatusBadge";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { JobDetail } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { TASK_KIND_LABELS } from "@/lib/stage-progress";

function taskDuration(startedAt: string | null, finishedAt: string | null): string | null {
  if (!startedAt || !finishedAt) return null;
  const seconds = (new Date(finishedAt).getTime() - new Date(startedAt).getTime()) / 1000;
  return formatDuration(seconds);
}

export function JobTasksTab({ job }: { job: JobDetail }) {
  return (
    <Table>
      <TableCaption className="sr-only">Pipeline stage timing and status</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Stage</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Attempts</TableHead>
          <TableHead>Duration</TableHead>
          <TableHead>Detail</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {job.tasks.map((task) => (
          <TableRow key={task.kind}>
            <TableCell className="font-medium">{TASK_KIND_LABELS[task.kind]}</TableCell>
            <TableCell>
              <StatusBadge status={task.status} size="sm" />
            </TableCell>
            <TableCell className="tabular-nums">{task.attempts}</TableCell>
            <TableCell className="tabular-nums">
              {taskDuration(task.started_at, task.finished_at) ?? "—"}
            </TableCell>
            <TableCell
              className={
                task.error ? "text-status-danger max-w-96" : "text-muted-foreground max-w-96"
              }
            >
              {task.error ?? task.message ?? "—"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
