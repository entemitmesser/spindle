import { useQuery } from "@tanstack/react-query";
import { createFileRoute, notFound } from "@tanstack/react-router";
import { useState } from "react";

import { IdentifyDialog } from "@/components/jobs/IdentifyDialog";
import { JobDetailHeader } from "@/components/jobs/JobDetailHeader";
import { JobEventsTab } from "@/components/jobs/JobEventsTab";
import { JobLogsTab } from "@/components/jobs/JobLogsTab";
import { JobOverviewTab } from "@/components/jobs/JobOverviewTab";
import { JobTasksTab } from "@/components/jobs/JobTasksTab";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, api } from "@/lib/api";
import { jobDisplayTitle } from "@/lib/format";
import { queryKeys } from "@/lib/query-keys";
import { usePageTitle } from "@/providers/PageTitleProvider";

export const Route = createFileRoute("/jobs/$jobId")({
  component: JobDetailPage,
  loader: async ({ params }) => {
    try {
      return await api.jobs.get(Number(params.jobId));
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) throw notFound();
      throw error;
    }
  },
});

function JobDetailPage() {
  const jobId = Number(Route.useParams().jobId);
  const initialData = Route.useLoaderData();
  const [editIdentity, setEditIdentity] = useState(false);

  const { data: job } = useQuery({
    queryKey: queryKeys.jobs.detail(jobId),
    queryFn: () => api.jobs.get(jobId),
    initialData,
  });

  usePageTitle(job ? jobDisplayTitle(job.title, job.year) : "Job");

  if (!job) {
    return <Skeleton className="h-64 w-full" />;
  }

  return (
    <div className="space-y-6">
      <JobDetailHeader job={job} onEditIdentity={() => setEditIdentity(true)} />

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="tasks">Tasks</TabsTrigger>
          <TabsTrigger value="logs">Logs</TabsTrigger>
          <TabsTrigger value="events">Events</TabsTrigger>
        </TabsList>
        <TabsContent value="overview">
          <JobOverviewTab job={job} />
        </TabsContent>
        <TabsContent value="tasks">
          <JobTasksTab job={job} />
        </TabsContent>
        <TabsContent value="logs">
          <JobLogsTab job={job} />
        </TabsContent>
        <TabsContent value="events">
          <JobEventsTab jobId={job.id} />
        </TabsContent>
      </Tabs>

      <IdentifyDialog
        jobId={editIdentity ? job.id : null}
        onOpenChange={(open) => setEditIdentity(open)}
      />
    </div>
  );
}
