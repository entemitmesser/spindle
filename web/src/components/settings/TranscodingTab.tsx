import { useQuery } from "@tanstack/react-query";
import type { Control } from "react-hook-form";

import { LangListInput } from "@/components/settings/LangListInput";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { Badge } from "@/components/ui/badge";
import { FormControl, FormField, FormItem, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import type { RuntimeConfigForm } from "@/lib/settings-schema";

const HARDWARE_ENCODERS = [
  { value: "auto", label: "Auto-detect" },
  { value: "none", label: "Software only" },
  { value: "qsv", label: "Intel Quick Sync" },
  { value: "nvenc", label: "NVIDIA NVENC" },
  { value: "vaapi", label: "VA-API" },
] as const;

export function TranscodingTab({ control }: { control: Control<RuntimeConfigForm> }) {
  const { data: presets } = useQuery({
    queryKey: queryKeys.settings.presets(),
    queryFn: api.settings.presets,
  });
  const { data: system } = useQuery({
    queryKey: queryKeys.system.info(),
    queryFn: api.system,
  });
  const availableEncoders = new Set(system?.encoders ?? []);

  return (
    <div>
      <FormField
        control={control}
        name="transcode.enabled"
        render={({ field }) => (
          <SettingsSection
            label="Transcode after rip"
            description="Re-encode ripped titles instead of keeping the raw disc remux."
          >
            <Switch checked={field.value} onCheckedChange={field.onChange} />
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="transcode.preset_movie"
        render={({ field }) => (
          <SettingsSection
            label="Movie/series preset"
            description="HandBrake preset used for standard-definition and 1080p sources."
          >
            <FormItem>
              <Select value={field.value} onValueChange={field.onChange}>
                <FormControl>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {(presets ?? []).map((preset) => (
                    <SelectItem key={preset.name} value={preset.name}>
                      {preset.name}
                      {preset.builtin ? "" : " (custom)"}
                    </SelectItem>
                  ))}
                  {!presets?.some((p) => p.name === field.value) && field.value && (
                    <SelectItem value={field.value}>{field.value}</SelectItem>
                  )}
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="transcode.preset_uhd"
        render={({ field }) => (
          <SettingsSection
            label="UHD preset"
            description="HandBrake preset used for 2160p / UHD Blu-ray sources."
          >
            <FormItem>
              <Select value={field.value} onValueChange={field.onChange}>
                <FormControl>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {(presets ?? []).map((preset) => (
                    <SelectItem key={preset.name} value={preset.name}>
                      {preset.name}
                      {preset.builtin ? "" : " (custom)"}
                    </SelectItem>
                  ))}
                  {!presets?.some((p) => p.name === field.value) && field.value && (
                    <SelectItem value={field.value}>{field.value}</SelectItem>
                  )}
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="transcode.hardware_encoder"
        render={({ field }) => (
          <SettingsSection
            label="Hardware encoder"
            description="Options this host hasn't probed as working are disabled."
          >
            <Select value={field.value} onValueChange={field.onChange}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {HARDWARE_ENCODERS.map((encoder) => {
                  const disabled =
                    encoder.value !== "auto" &&
                    encoder.value !== "none" &&
                    !availableEncoders.has(encoder.value);
                  const item = (
                    <SelectItem key={encoder.value} value={encoder.value} disabled={disabled}>
                      {encoder.label}
                    </SelectItem>
                  );
                  if (!disabled) return item;
                  return (
                    <Tooltip key={encoder.value}>
                      <TooltipTrigger asChild>
                        <span>{item}</span>
                      </TooltipTrigger>
                      <TooltipContent>Not detected on this host</TooltipContent>
                    </Tooltip>
                  );
                })}
              </SelectContent>
            </Select>
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="transcode.quality"
        render={({ field }) => (
          <SettingsSection
            label="Quality"
            description="Lower keeps more detail at a larger file size."
          >
            <div className="space-y-2">
              <Slider
                min={0}
                max={51}
                step={1}
                value={[field.value]}
                onValueChange={([v]) => field.onChange(v)}
              />
              <div className="text-muted-foreground flex justify-between text-xs">
                <span>Better quality</span>
                <Badge variant="outline" className="font-mono">
                  {field.value}
                </Badge>
                <span>Smaller file</span>
              </div>
            </div>
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="transcode.audio_langs"
        render={({ field }) => (
          <SettingsSection
            label="Audio languages to keep"
            description="ISO 639-2 codes, in priority order."
          >
            <LangListInput value={field.value} onChange={field.onChange} />
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="transcode.keep_original_audio"
        render={({ field }) => (
          <SettingsSection
            label="Keep original audio track"
            description="Always retain the disc's original-language track even if it's not in the list above."
          >
            <Switch checked={field.value} onCheckedChange={field.onChange} />
          </SettingsSection>
        )}
      />

      <FormField
        control={control}
        name="transcode.workers"
        render={({ field }) => (
          <SettingsSection
            label="Parallel transcode workers"
            description="More than 1 is usually slower — CPU/GPU bound work rarely benefits from parallelism."
          >
            <Input
              type="number"
              min={1}
              max={8}
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
