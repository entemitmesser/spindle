import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { formatBytes, formatDuration } from "@/lib/format";

function DiscInfo({ job }: { job: JobDetail }) {
  const disc = job.disc;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Disc</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {!disc && <p className="text-muted-foreground">Manual job — no source disc.</p>}
        {disc && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5">
            <dt className="text-muted-foreground">Type</dt>
            <dd className="uppercase">{disc.disc_type.replace("_", " ")}</dd>
            <dt className="text-muted-foreground">Label</dt>
            <dd className="font-mono">{disc.label ?? "—"}</dd>
            <dt className="text-muted-foreground">Size</dt>
            <dd>{disc.size_bytes ? formatBytes(disc.size_bytes) : "—"}</dd>
            <dt className="text-muted-foreground">Detected</dt>
            <dd>{new Date(disc.detected_at).toLocaleString()}</dd>
          </dl>
        )}
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 border-t pt-2">
          <dt className="text-muted-foreground">Created</dt>
          <dd>{new Date(job.created_at).toLocaleString()}</dd>
        </dl>
      </CardContent>
    </Card>
  );
}

function MediaFilesTable({ job }: { job: JobDetail }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Files</CardTitle>
      </CardHeader>
      <CardContent>
        {job.media_files.length === 0 ? (
          <p className="text-muted-foreground text-sm">No files recorded yet.</p>
        ) : (
          <Table>
            <TableCaption className="sr-only">Source and output media files</TableCaption>
            <TableHeader>
              <TableRow>
                <TableHead>Path</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Size</TableHead>
                <TableHead>Video</TableHead>
                <TableHead>Audio</TableHead>
                <TableHead>Duration</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {job.media_files.map((file) => (
                <TableRow key={file.id}>
                  <TableCell className="max-w-64 truncate font-mono text-xs" title={file.path}>
                    {file.path}
                  </TableCell>
                  <TableCell className="capitalize">{file.role}</TableCell>
                  <TableCell className="tabular-nums">
                    {file.size_bytes ? formatBytes(file.size_bytes) : "—"}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {file.video_codec ?? "—"}
                    {file.width && file.height ? ` ${file.width}×${file.height}` : ""}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{file.audio_codec ?? "—"}</TableCell>
                  <TableCell className="tabular-nums">
                    {formatDuration(file.duration_seconds) ?? "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

export function JobOverviewTab({ job }: { job: JobDetail }) {
  return (
    <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
      <DiscInfo job={job} />
      <MediaFilesTable job={job} />
    </div>
  );
}
