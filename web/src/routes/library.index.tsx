import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { FolderOpen } from "lucide-react";

import { EmptyState } from "@/components/common/EmptyState";
import { PosterImage } from "@/components/common/PosterImage";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { jobDisplayTitle } from "@/lib/format";
import { queryKeys } from "@/lib/query-keys";
import { usePageTitle } from "@/providers/PageTitleProvider";

export const Route = createFileRoute("/library/")({
  component: LibraryPage,
});

function LibraryPage() {
  usePageTitle("Library");
  const { data, isPending } = useQuery({
    queryKey: queryKeys.jobs.list({ status: ["completed"], limit: 60 }),
    queryFn: () => api.jobs.list({ status: ["completed"], limit: 60 }),
  });

  if (isPending) {
    return (
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
        {Array.from({ length: 12 }).map((_, i) => (
          <Skeleton key={i} className="aspect-2/3 w-full" />
        ))}
      </div>
    );
  }

  if (!data || data.items.length === 0) {
    return (
      <EmptyState
        icon={FolderOpen}
        title="Nothing in the library yet."
        description="Completed jobs show up here once they're organized."
      />
    );
  }

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
      {data.items.map((job) => (
        <Link
          key={job.id}
          to="/jobs/$jobId"
          params={{ jobId: String(job.id) }}
          className="group space-y-1.5"
        >
          <PosterImage
            src={job.poster_url}
            alt={jobDisplayTitle(job.title, job.year)}
            className="group-hover:ring-primary aspect-2/3 w-full rounded-lg transition-shadow group-hover:ring-2"
          />
          <p className="truncate text-sm font-medium">{jobDisplayTitle(job.title, job.year)}</p>
        </Link>
      ))}
    </div>
  );
}
