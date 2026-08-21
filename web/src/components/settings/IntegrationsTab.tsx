import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";
import type { Control } from "react-hook-form";

import { SettingsSection } from "@/components/settings/SettingsSection";
import { Button } from "@/components/ui/button";
import { FormControl, FormField, FormItem, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { type SettingsTestTarget, api } from "@/lib/api";
import type { RuntimeConfigForm } from "@/lib/settings-schema";

const TARGETS: { target: SettingsTestTarget; label: string; description: string }[] = [
  { target: "tmdb", label: "TMDB", description: "Metadata search, posters, artwork." },
  { target: "opensubtitles", label: "OpenSubtitles", description: "Sidecar subtitle downloads." },
  { target: "jellyfin", label: "Jellyfin", description: "Library refresh after organize." },
  { target: "notify", label: "Notifications", description: "The webhook configured below." },
];

function TestConnectionRow({
  target,
  label,
  description,
}: {
  target: SettingsTestTarget;
  label: string;
  description: string;
}) {
  const test = useMutation({ mutationFn: () => api.settings.test(target) });
  const result = test.data;

  return (
    <SettingsSection label={label} description={description}>
      <div className="flex items-center gap-3">
        <Button variant="outline" size="sm" disabled={test.isPending} onClick={() => test.mutate()}>
          {test.isPending ? "Testing…" : "Test connection"}
        </Button>
        {result && (
          <span className="flex items-center gap-1.5 text-sm">
            {result.ok ? (
              <CheckCircle2 className="text-status-success size-4" />
            ) : (
              <XCircle className="text-status-danger size-4" />
            )}
            {result.message}
            {result.latency_ms !== null && result.latency_ms !== undefined && (
              <span className="text-muted-foreground tabular-nums">({result.latency_ms}ms)</span>
            )}
          </span>
        )}
        {test.isError && <span className="text-status-danger text-sm">Request failed.</span>}
      </div>
    </SettingsSection>
  );
}

const NOTIFY_ON_OPTIONS = [
  { value: "needs_review", label: "Needs review" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
  { value: "system_warning", label: "System warning" },
] as const;

export function IntegrationsTab({ control }: { control: Control<RuntimeConfigForm> }) {
  return (
    <div>
      {TARGETS.filter((t) => t.target !== "notify").map((t) => (
        <TestConnectionRow key={t.target} {...t} />
      ))}

      <FormField
        control={control}
        name="notifications.enabled"
        render={({ field }) => (
          <SettingsSection
            label="Webhook notifications"
            description="Send a webhook when a job needs attention or finishes."
          >
            <Switch checked={field.value} onCheckedChange={field.onChange} />
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="notifications.webhook_url"
        render={({ field }) => (
          <SettingsSection
            label="Webhook URL"
            description="Receives a POST with the job.completed payload."
          >
            <FormItem>
              <FormControl>
                <Input
                  {...field}
                  value={field.value ?? ""}
                  placeholder="https://example.com/hooks/spindle"
                  className="font-mono"
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="notifications.notify_on"
        render={({ field }) => (
          <SettingsSection
            label="Notify on"
            description="Which job/system transitions trigger the webhook."
          >
            <div className="flex flex-wrap gap-3">
              {NOTIFY_ON_OPTIONS.map((option) => {
                const checked = field.value.includes(option.value);
                return (
                  <label key={option.value} className="flex items-center gap-1.5 text-sm">
                    <Switch
                      checked={checked}
                      onCheckedChange={(next) =>
                        field.onChange(
                          next
                            ? [...field.value, option.value]
                            : field.value.filter((v: string) => v !== option.value),
                        )
                      }
                    />
                    {option.label}
                  </label>
                );
              })}
            </div>
          </SettingsSection>
        )}
      />

      <TestConnectionRow
        target="notify"
        label="Test webhook"
        description="Sends a test payload to the URL above."
      />
    </div>
  );
}
