import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { Disc3, HardDrive } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/common/EmptyState";
import { DriveCard } from "@/components/drives/DriveCard";
import { IdentifyDialog } from "@/components/jobs/IdentifyDialog";
import { JobRow } from "@/components/jobs/JobRow";
import { QueueList } from "@/components/jobs/QueueList";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { type JobSummary, api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import { usePageTitle } from "@/providers/PageTitleProvider";

export const Route = createFileRoute("/")({
  component: Dashboard,
});

function DriveGridSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <Skeleton key={i} className="h-32 w-full" />
      ))}
    </div>
  );
}

function Dashboard() {
  usePageTitle("Dashboard");
  const [reviewJobId, setReviewJobId] = useState<number | null>(null);

  const drives = useQuery({ queryKey: queryKeys.drives.list(), queryFn: api.drives.list });
  const activeJobs = useQuery({
    queryKey: queryKeys.jobs.list({ status: ["running", "needs_review"] }),
    queryFn: () => api.jobs.list({ status: ["running", "needs_review"] }),
  });
  const recentlyCompleted = useQuery({
    queryKey: queryKeys.jobs.list({ status: ["completed"], limit: 5 }),
    queryFn: () => api.jobs.list({ status: ["completed"], limit: 5 }),
  });

  function handleReview(job: JobSummary) {
    setReviewJobId(job.id);
  }

  return (
    <>
      <section className="space-y-3">
        <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Drives
        </h2>
        {drives.isPending && <DriveGridSkeleton />}
        {drives.isSuccess && drives.data.items.length === 0 && (
          <EmptyState
            icon={HardDrive}
            title="No optical drives detected"
            description={
              <>
                Check that the container has access to <code>/dev/sr0</code> (device mapping in your
                compose file).
              </>
            }
          />
        )}
        {drives.isSuccess && drives.data.items.length > 0 && (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {drives.data.items.map((drive) => (
              <DriveCard key={drive.id} drive={drive} />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Active jobs
        </h2>
        {activeJobs.isPending && (
          <div className="space-y-2">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        )}
        {activeJobs.isSuccess && activeJobs.data.items.length === 0 && (
          <EmptyState icon={Disc3} title="Nothing ripping right now." />
        )}
        {activeJobs.isSuccess && activeJobs.data.items.length > 0 && (
          <div className="space-y-2">
            {activeJobs.data.items.map((job) => (
              <JobRow key={job.id} job={job} onReview={handleReview} />
            ))}
          </div>
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Queue</CardTitle>
          </CardHeader>
          <CardContent>
            <QueueList />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle>Recently completed</CardTitle>
            <Button variant="ghost" size="sm" asChild>
              <Link to="/jobs">History</Link>
            </Button>
          </CardHeader>
          <CardContent className="space-y-2">
            {recentlyCompleted.isPending && <Skeleton className="h-11 w-full" />}
            {recentlyCompleted.isSuccess && recentlyCompleted.data.items.length === 0 && (
              <p className="text-muted-foreground text-sm">
                Nothing ripped yet. Insert a disc to start.
              </p>
            )}
            {recentlyCompleted.data?.items.map((job) => (
              <JobRow key={job.id} job={job} />
            ))}
          </CardContent>
        </Card>
      </div>

      <IdentifyDialog jobId={reviewJobId} onOpenChange={(open) => !open && setReviewJobId(null)} />
    </>
  );
}
