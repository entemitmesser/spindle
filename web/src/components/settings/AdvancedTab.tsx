import type { Control } from "react-hook-form";

import { SettingsSection } from "@/components/settings/SettingsSection";
import { FormField } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import type { RuntimeConfigForm } from "@/lib/settings-schema";

function octal(n: number): string {
  return n.toString(8).padStart(3, "0");
}

export function AdvancedTab({ control }: { control: Control<RuntimeConfigForm> }) {
  return (
    <div>
      <FormField
        control={control}
        name="rip.min_title_seconds"
        render={({ field }) => (
          <SettingsSection
            label="Minimum title length"
            description="Titles shorter than this (in minutes) are treated as menus/extras, not the main feature."
          >
            <div className="flex items-center gap-2">
              <Input
                type="number"
                min={0}
                value={Math.round(field.value / 60)}
                onChange={(e) => field.onChange(Number(e.target.value) * 60)}
                className="w-24"
              />
              <span className="text-muted-foreground text-sm">minutes</span>
            </div>
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="rip.max_title_seconds"
        render={({ field }) => (
          <SettingsSection
            label="Maximum title length"
            description="Titles longer than this (in minutes) are treated as a compilation, not a single feature."
          >
            <div className="flex items-center gap-2">
              <Input
                type="number"
                min={0}
                value={Math.round(field.value / 60)}
                onChange={(e) => field.onChange(Number(e.target.value) * 60)}
                className="w-24"
              />
              <span className="text-muted-foreground text-sm">minutes</span>
            </div>
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="behaviour.min_free_bytes"
        render={({ field }) => (
          <SettingsSection
            label="Minimum free space"
            description="Stop starting new rips/transcodes below this much free space on the work volume."
          >
            <div className="flex items-center gap-2">
              <Input
                type="number"
                min={0}
                value={Math.round(field.value / 1024 ** 3)}
                onChange={(e) => field.onChange(Number(e.target.value) * 1024 ** 3)}
                className="w-24"
              />
              <span className="text-muted-foreground text-sm">GB</span>
            </div>
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="behaviour.max_retries"
        render={({ field }) => (
          <SettingsSection
            label="Max automatic retries"
            description="Transient failures (network, subtitles) retry with backoff up to this many times. Rip/transcode failures always need an explicit retry."
          >
            <Input
              type="number"
              min={0}
              max={10}
              value={field.value}
              onChange={(e) => field.onChange(Number(e.target.value))}
              className="w-24"
            />
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="library.file_mode"
        render={({ field }) => (
          <SettingsSection
            label="File permissions"
            description="Octal mode applied to files written into the library."
          >
            <Input
              value={octal(field.value)}
              onChange={(e) => {
                const parsed = Number.parseInt(e.target.value, 8);
                if (!Number.isNaN(parsed)) field.onChange(parsed);
              }}
              className="w-24 font-mono"
            />
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="library.dir_mode"
        render={({ field }) => (
          <SettingsSection
            label="Directory permissions"
            description="Octal mode applied to directories created in the library."
          >
            <Input
              value={octal(field.value)}
              onChange={(e) => {
                const parsed = Number.parseInt(e.target.value, 8);
                if (!Number.isNaN(parsed)) field.onChange(parsed);
              }}
              className="w-24 font-mono"
            />
          </SettingsSection>
        )}
      />
    </div>
  );
}
