import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, HardDrive } from "lucide-react";

import { api } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { queryKeys } from "@/lib/query-keys";

/**
 * Header banner for the resilience pass (phase-09-hardening.md task 2):
 * a below-threshold disk (computed client-side from `GET /system`'s live
 * `disk` figures against `GET /settings`'s `behaviour.min_free_bytes`, so it
 * reflects the *current* state rather than a possibly-stale past event) plus
 * the most recent other `system.warning` codes, deduped.
 */
export function SystemWarningsBanner() {
  const { data: system } = useQuery({
    queryKey: queryKeys.system.info(),
    queryFn: api.system,
    refetchInterval: 30000,
  });
  const { data: settings } = useQuery({
    queryKey: queryKeys.settings.get(),
    queryFn: api.settings.get,
  });

  if (!system) return null;

  const minFreeBytes = settings?.behaviour.min_free_bytes ?? 0;
  const lowDisk = system.disk.filter((d) => d.free_bytes < minFreeBytes);
  const otherCodes = new Set<string>();
  const otherMessages: string[] = [];
  for (const warning of system.warnings) {
    if (warning.code === "low_disk_space" || otherCodes.has(warning.code)) continue;
    otherCodes.add(warning.code);
    otherMessages.push(warning.message);
  }

  if (lowDisk.length === 0 && otherMessages.length === 0) return null;

  return (
    <div className="border-status-warning/40 bg-status-warning/10 text-status-warning flex flex-col gap-1 border-b px-4 py-2 text-sm md:px-6">
      {lowDisk.map((d) => (
        <div key={d.label} className="flex items-center gap-2">
          <HardDrive className="size-4 shrink-0" aria-hidden="true" />
          <span>
            Low disk space on the {d.label} volume — only {formatBytes(d.free_bytes)} free.
          </span>
        </div>
      ))}
      {otherMessages.map((message) => (
        <div key={message} className="flex items-center gap-2">
          <AlertTriangle className="size-4 shrink-0" aria-hidden="true" />
          <span>{message}</span>
        </div>
      ))}
    </div>
  );
}
