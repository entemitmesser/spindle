import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef, useState } from "react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

type Level = "all" | "error" | "warn" | "info";

const LEVEL_PATTERNS: Record<Exclude<Level, "all">, RegExp> = {
  error: /\b(error|err|fatal|critical)\b/i,
  warn: /\b(warn|warning)\b/i,
  info: /\b(info|debug)\b/i,
};

function lineLevelClass(line: string): string | undefined {
  if (LEVEL_PATTERNS.error.test(line)) return "text-status-danger";
  if (LEVEL_PATTERNS.warn.test(line)) return "text-status-warning";
  return undefined;
}

interface LogViewerProps {
  lines: string[];
  emptyMessage?: string;
  className?: string;
}

/** Virtualized, mono, follow-tail log viewer (ui-design.md §4). Raw tool
 * output is welcome here, unsummarized — the one place that's true. */
export function LogViewer({
  lines,
  emptyMessage = "No log output yet.",
  className,
}: LogViewerProps) {
  const [follow, setFollow] = useState(true);
  const [level, setLevel] = useState<Level>("all");
  const parentRef = useRef<HTMLDivElement>(null);

  const filtered = level === "all" ? lines : lines.filter((l) => LEVEL_PATTERNS[level].test(l));

  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 20,
    overscan: 30,
  });

  useEffect(() => {
    if (follow && filtered.length > 0) {
      virtualizer.scrollToIndex(filtered.length - 1, { align: "end" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-run whenever new lines arrive, not on virtualizer identity
  }, [filtered.length, follow]);

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs">
            <Switch checked={follow} onCheckedChange={setFollow} />
            Follow
          </label>
          <Select value={level} onValueChange={(v) => setLevel(v as Level)}>
            <SelectTrigger size="sm" className="w-28">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All levels</SelectItem>
              <SelectItem value="error">Error</SelectItem>
              <SelectItem value="warn">Warning</SelectItem>
              <SelectItem value="info">Info</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <span className="text-muted-foreground text-xs tabular-nums">{filtered.length} lines</span>
      </div>

      <div
        ref={parentRef}
        className="bg-muted/40 h-96 overflow-auto rounded-lg border font-mono text-xs"
      >
        {filtered.length === 0 ? (
          <p className="text-muted-foreground p-4">{emptyMessage}</p>
        ) : (
          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualizer.getVirtualItems().map((row) => (
              <div
                key={row.index}
                className={cn(
                  "absolute top-0 left-0 w-full px-3 whitespace-pre",
                  lineLevelClass(filtered[row.index]),
                )}
                style={{ height: row.size, transform: `translateY(${row.start}px)` }}
              >
                {filtered[row.index]}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
