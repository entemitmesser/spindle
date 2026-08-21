import { z } from "zod";

import { validateTemplate } from "@/lib/naming-template";

const templateField = z
  .string()
  .min(1, "Required")
  .superRefine((value, ctx) => {
    const error = validateTemplate(value);
    if (error) ctx.addIssue({ code: "custom", message: error });
  });

export const ripConfigSchema = z.object({
  backend: z.enum(["makemkv", "handbrake"]),
  min_title_seconds: z.number().int().min(0),
  max_title_seconds: z.number().int().min(0),
  title_selection: z.enum(["longest", "all", "ask"]),
  rip_extras: z.boolean(),
  keep_raw_after_transcode: z.boolean(),
});

export const transcodeConfigSchema = z.object({
  enabled: z.boolean(),
  preset_movie: z.string().min(1, "Required"),
  preset_uhd: z.string().min(1, "Required"),
  hardware_encoder: z.enum(["auto", "none", "qsv", "nvenc", "vaapi"]),
  quality: z.number().min(0).max(51),
  audio_langs: z.array(z.string()),
  keep_original_audio: z.boolean(),
  workers: z.number().int().min(1).max(8),
});

export const subtitleConfigSchema = z.object({
  enabled: z.boolean(),
  langs: z.array(z.string()),
  keep_forced_from_disc: z.boolean(),
  external_provider: z.enum(["opensubtitles", "none"]),
  hearing_impaired: z.enum(["include", "exclude", "only"]),
});

export const libraryConfigSchema = z.object({
  movies_dir: z.string().min(1, "Required"),
  series_dir: z.string().min(1, "Required"),
  movie_dir_template: templateField,
  movie_file_template: templateField,
  series_dir_template: templateField,
  series_file_template: templateField,
  include_tmdb_id: z.boolean(),
  on_conflict: z.enum(["review", "overwrite", "keep_both"]),
  write_nfo: z.boolean(),
  file_mode: z.number().int().min(0).max(0o777),
  dir_mode: z.number().int().min(0).max(0o777),
});

export const behaviourConfigSchema = z.object({
  auto_start: z.boolean(),
  auto_eject: z.boolean(),
  identify_confidence_threshold: z.number().min(0).max(1),
  min_free_bytes: z.number().int().min(0),
  completed_retention_days: z.number().int().min(0),
  max_retries: z.number().int().min(0).max(10),
});

export const notificationConfigSchema = z.object({
  enabled: z.boolean(),
  webhook_url: z.string().url("Must be a valid URL").nullish().or(z.literal("")),
  notify_on: z.array(z.enum(["needs_review", "completed", "failed", "system_warning"])),
});

export const runtimeConfigSchema = z.object({
  rip: ripConfigSchema,
  transcode: transcodeConfigSchema,
  subtitles: subtitleConfigSchema,
  library: libraryConfigSchema,
  behaviour: behaviourConfigSchema,
  notifications: notificationConfigSchema,
});

export type RuntimeConfigForm = z.infer<typeof runtimeConfigSchema>;
