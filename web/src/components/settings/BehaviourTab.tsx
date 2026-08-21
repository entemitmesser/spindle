import type { Control } from "react-hook-form";

import { SettingsSection } from "@/components/settings/SettingsSection";
import { FormField } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import type { RuntimeConfigForm } from "@/lib/settings-schema";

export function BehaviourTab({ control }: { control: Control<RuntimeConfigForm> }) {
  return (
    <div>
      <FormField
        control={control}
        name="behaviour.auto_start"
        render={({ field }) => (
          <SettingsSection
            label="Auto-start jobs"
            description="Begin ripping automatically when a disc is inserted."
          >
            <Switch checked={field.value} onCheckedChange={field.onChange} />
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="behaviour.auto_eject"
        render={({ field }) => (
          <SettingsSection
            label="Auto-eject on success"
            description="Eject the disc once ripping finishes."
          >
            <Switch checked={field.value} onCheckedChange={field.onChange} />
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="behaviour.identify_confidence_threshold"
        render={({ field }) => (
          <SettingsSection
            label="Identify confidence threshold"
            description="Below this match score, a job is parked in needs review instead of auto-accepted."
          >
            <div className="space-y-2">
              <Slider
                min={0}
                max={1}
                step={0.05}
                value={[field.value]}
                onValueChange={([v]) => field.onChange(v)}
              />
              <p className="text-muted-foreground text-xs tabular-nums">
                {Math.round(field.value * 100)}%
              </p>
            </div>
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="behaviour.completed_retention_days"
        render={({ field }) => (
          <SettingsSection
            label="Completed job retention"
            description="Days to keep a completed job's work directory before cleanup deletes it."
          >
            <Input
              type="number"
              min={0}
              value={field.value}
              onChange={(e) => field.onChange(Number(e.target.value))}
              className="w-24"
            />
          </SettingsSection>
        )}
      />
    </div>
  );
}
