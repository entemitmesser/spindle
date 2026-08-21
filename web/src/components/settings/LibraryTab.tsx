import type { Control } from "react-hook-form";
import { useWatch } from "react-hook-form";

import { SettingsSection } from "@/components/settings/SettingsSection";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FormControl, FormField, FormItem, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { renderTemplatePreview } from "@/lib/naming-template";
import type { RuntimeConfigForm } from "@/lib/settings-schema";

function TemplateField({
  control,
  name,
  label,
  description,
}: {
  control: Control<RuntimeConfigForm>;
  name:
    | "library.movies_dir"
    | "library.series_dir"
    | "library.movie_dir_template"
    | "library.movie_file_template"
    | "library.series_dir_template"
    | "library.series_file_template";
  label: string;
  description?: string;
}) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <SettingsSection label={label} description={description}>
          <FormItem>
            <FormControl>
              <Input {...field} className="font-mono" />
            </FormControl>
            <FormMessage />
          </FormItem>
        </SettingsSection>
      )}
    />
  );
}

function LivePreview({ control }: { control: Control<RuntimeConfigForm> }) {
  const [moviesDir, movieDirTpl, movieFileTpl, seriesDir, seriesDirTpl, seriesFileTpl] = useWatch({
    control,
    name: [
      "library.movies_dir",
      "library.movie_dir_template",
      "library.movie_file_template",
      "library.series_dir",
      "library.series_dir_template",
      "library.series_file_template",
    ],
  });

  const moviePath = `${moviesDir}/${renderTemplatePreview(movieDirTpl)}/${renderTemplatePreview(movieFileTpl)}.mkv`;
  const seriesPath = `${seriesDir}/${renderTemplatePreview(seriesDirTpl)}/${renderTemplatePreview(seriesFileTpl)}.mkv`;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Live preview</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 font-mono text-xs">
        <div>
          <p className="text-muted-foreground mb-1 text-[11px] tracking-wide uppercase">Movie</p>
          <p className="bg-muted rounded-md p-2 break-all">{moviePath}</p>
        </div>
        <div>
          <p className="text-muted-foreground mb-1 text-[11px] tracking-wide uppercase">Series</p>
          <p className="bg-muted rounded-md p-2 break-all">{seriesPath}</p>
        </div>
      </CardContent>
    </Card>
  );
}

export function LibraryTab({ control }: { control: Control<RuntimeConfigForm> }) {
  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
      <div>
        <TemplateField
          control={control}
          name="library.movies_dir"
          label="Movies folder"
          description="Relative to the library root."
        />
        <TemplateField
          control={control}
          name="library.movie_dir_template"
          label="Movie directory template"
        />
        <TemplateField
          control={control}
          name="library.movie_file_template"
          label="Movie file template"
        />
        <TemplateField
          control={control}
          name="library.series_dir"
          label="Series folder"
          description="Relative to the library root."
        />
        <TemplateField
          control={control}
          name="library.series_dir_template"
          label="Series directory template"
        />
        <TemplateField
          control={control}
          name="library.series_file_template"
          label="Series file template"
        />

        <FormField
          control={control}
          name="library.include_tmdb_id"
          render={({ field }) => (
            <SettingsSection
              label="Include TMDB ID suffix"
              description="Appends [tmdbid-…] for Jellyfin's exact matching."
            >
              <Switch checked={field.value} onCheckedChange={field.onChange} />
            </SettingsSection>
          )}
        />

        <FormField
          control={control}
          name="library.on_conflict"
          render={({ field }) => (
            <SettingsSection
              label="On file conflict"
              description="What happens when the destination already has a different file."
            >
              <FormItem>
                <Select value={field.value} onValueChange={field.onChange}>
                  <FormControl>
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    <SelectItem value="review">Park in needs review</SelectItem>
                    <SelectItem value="overwrite">Overwrite (quarantine the old file)</SelectItem>
                    <SelectItem value="keep_both">Keep both</SelectItem>
                  </SelectContent>
                </Select>
                <FormMessage />
              </FormItem>
            </SettingsSection>
          )}
        />

        <FormField
          control={control}
          name="library.write_nfo"
          render={({ field }) => (
            <SettingsSection
              label="Write NFO files"
              description="Emit a Kodi/Jellyfin-style .nfo alongside each item."
            >
              <Switch checked={field.value} onCheckedChange={field.onChange} />
            </SettingsSection>
          )}
        />
      </div>

      <div className="lg:sticky lg:top-20 lg:self-start">
        <LivePreview control={control} />
      </div>
    </div>
  );
}
