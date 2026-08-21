import type { Control } from "react-hook-form";

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

export function RippingTab({ control }: { control: Control<RuntimeConfigForm> }) {
  return (
    <div>
      <FormField
        control={control}
        name="rip.backend"
        render={({ field }) => (
          <SettingsSection
            label="Rip backend"
            description="Which tool actually pulls titles off the disc."
          >
            <FormItem>
              <Select value={field.value} onValueChange={field.onChange}>
                <FormControl>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  <SelectItem value="makemkv">MakeMKV</SelectItem>
                  <SelectItem value="handbrake">HandBrake</SelectItem>
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="rip.title_selection"
        render={({ field }) => (
          <SettingsSection
            label="Title selection"
            description="Which titles on a disc get ripped when there's more than one candidate."
          >
            <FormItem>
              <Select value={field.value} onValueChange={field.onChange}>
                <FormControl>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  <SelectItem value="longest">Longest only</SelectItem>
                  <SelectItem value="all">All plausible titles</SelectItem>
                  <SelectItem value="ask">Ask me every time</SelectItem>
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="rip.rip_extras"
        render={({ field }) => (
          <SettingsSection
            label="Rip extras"
            description="Also rip bonus/extra titles, not just the main feature."
          >
            <Switch checked={field.value} onCheckedChange={field.onChange} />
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="rip.keep_raw_after_transcode"
        render={({ field }) => (
          <SettingsSection
            label="Keep raw rip after transcode"
            description="Don't delete the MakeMKV output from work/raw/ once transcoding succeeds."
          >
            <Switch checked={field.value} onCheckedChange={field.onChange} />
          </SettingsSection>
        )}
      />
    </div>
  );
}
