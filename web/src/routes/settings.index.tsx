import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { AdvancedTab } from "@/components/settings/AdvancedTab";
import { BehaviourTab } from "@/components/settings/BehaviourTab";
import { IntegrationsTab } from "@/components/settings/IntegrationsTab";
import { LibraryTab } from "@/components/settings/LibraryTab";
import { RippingTab } from "@/components/settings/RippingTab";
import { SubtitlesTab } from "@/components/settings/SubtitlesTab";
import { TranscodingTab } from "@/components/settings/TranscodingTab";
import { Button } from "@/components/ui/button";
import { Form } from "@/components/ui/form";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import { type RuntimeConfigForm, runtimeConfigSchema } from "@/lib/settings-schema";
import { usePageTitle } from "@/providers/PageTitleProvider";

export const Route = createFileRoute("/settings/")({
  component: SettingsPage,
});

function SettingsForm({ initial }: { initial: RuntimeConfigForm }) {
  const queryClient = useQueryClient();
  // `defaultValues` only, deliberately no `values:` sync — a background
  // refetch (e.g. from another tab's `settings.updated` event) must never
  // clobber changes the user hasn't saved yet.
  const form = useForm<RuntimeConfigForm>({
    resolver: zodResolver(runtimeConfigSchema),
    defaultValues: initial,
  });

  async function onSubmit(values: RuntimeConfigForm) {
    try {
      const updated = await api.settings.update(values);
      toast.success("Settings saved.");
      queryClient.setQueryData(queryKeys.settings.get(), updated);
      form.reset(updated as RuntimeConfigForm);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Couldn't save settings.");
    }
  }

  const { isDirty, isSubmitting } = form.formState;

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4 pb-20">
        <Tabs defaultValue="ripping">
          <TabsList>
            <TabsTrigger value="ripping">Ripping</TabsTrigger>
            <TabsTrigger value="transcoding">Transcoding</TabsTrigger>
            <TabsTrigger value="subtitles">Subtitles</TabsTrigger>
            <TabsTrigger value="library">Library</TabsTrigger>
            <TabsTrigger value="integrations">Integrations</TabsTrigger>
            <TabsTrigger value="behaviour">Behaviour</TabsTrigger>
            <TabsTrigger value="advanced">Advanced</TabsTrigger>
          </TabsList>
          <TabsContent value="ripping">
            <RippingTab control={form.control} />
          </TabsContent>
          <TabsContent value="transcoding">
            <TranscodingTab control={form.control} />
          </TabsContent>
          <TabsContent value="subtitles">
            <SubtitlesTab control={form.control} />
          </TabsContent>
          <TabsContent value="library">
            <LibraryTab control={form.control} />
          </TabsContent>
          <TabsContent value="integrations">
            <IntegrationsTab control={form.control} />
          </TabsContent>
          <TabsContent value="behaviour">
            <BehaviourTab control={form.control} />
          </TabsContent>
          <TabsContent value="advanced">
            <AdvancedTab control={form.control} />
          </TabsContent>
        </Tabs>

        {isDirty && (
          <div className="bg-card/95 border-border fixed inset-x-0 bottom-0 z-20 flex justify-center border-t px-4 py-3 backdrop-blur-sm md:pl-14">
            <div className="flex w-full max-w-[1400px] items-center justify-between">
              <p className="text-muted-foreground text-sm">You have unsaved changes.</p>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => form.reset(initial)}
                  disabled={isSubmitting}
                >
                  Discard
                </Button>
                <Button type="submit" disabled={isSubmitting}>
                  Save changes
                </Button>
              </div>
            </div>
          </div>
        )}
      </form>
    </Form>
  );
}

function SettingsPage() {
  usePageTitle("Settings");
  const { data, isPending } = useQuery({
    queryKey: queryKeys.settings.get(),
    queryFn: api.settings.get,
  });

  if (isPending || !data) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  return <SettingsForm initial={data} />;
}
