import type { Control } from "react-hook-form";

import { LangListInput } from "@/components/settings/LangListInput";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { FormControl, FormField, FormItem, FormMessage } from "@/components/ui/form";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { RuntimeConfigForm } from "@/lib/settings-schema";

export function SubtitlesTab({ control }: { control: Control<RuntimeConfigForm> }) {
  return (
    <div>
      <FormField
        control={control}
        name="subtitles.enabled"
        render={({ field }) => (
          <SettingsSection
            label="Fetch subtitles"
            description="Download sidecar subtitles after transcoding."
          >
            <Switch checked={field.value} onCheckedChange={field.onChange} />
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="subtitles.langs"
        render={({ field }) => (
          <SettingsSection
            label="Wanted languages"
            description="ISO 639-2 codes, in priority order."
          >
            <LangListInput value={field.value} onChange={field.onChange} />
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="subtitles.keep_forced_from_disc"
        render={({ field }) => (
          <SettingsSection
            label="Keep forced disc subtitles"
            description="Retain forced subtitle tracks that were embedded on the disc."
          >
            <Switch checked={field.value} onCheckedChange={field.onChange} />
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="subtitles.external_provider"
        render={({ field }) => (
          <SettingsSection
            label="External provider"
            description="Where sidecar subtitles are fetched from."
          >
            <FormItem>
              <Select value={field.value} onValueChange={field.onChange}>
                <FormControl>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  <SelectItem value="opensubtitles">OpenSubtitles</SelectItem>
                  <SelectItem value="none">None</SelectItem>
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="subtitles.hearing_impaired"
        render={({ field }) => (
          <SettingsSection
            label="Hearing-impaired subtitles"
            description="How to treat SDH/HI-tagged subtitle results."
          >
            <FormItem>
              <Select value={field.value} onValueChange={field.onChange}>
                <FormControl>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  <SelectItem value="include">Include</SelectItem>
                  <SelectItem value="exclude">Exclude</SelectItem>
                  <SelectItem value="only">Only hearing-impaired</SelectItem>
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          </SettingsSection>
        )}
      />
    </div>
  );
}
