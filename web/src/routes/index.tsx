import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/")({
  component: Dashboard,
});

function Dashboard() {
  const { data, isPending, isError } = useQuery({
    queryKey: queryKeys.system.health(),
    queryFn: api.health,
    refetchInterval: 5000,
  });

  return (
    <Card className="max-w-sm">
      <CardHeader>
        <CardTitle>Connected</CardTitle>
        <CardDescription>Backend health check</CardDescription>
      </CardHeader>
      <CardContent className="flex items-center gap-3">
        <span
          className={cn(
            "h-2.5 w-2.5 shrink-0 rounded-full",
            isError && "bg-status-danger",
            isPending && "bg-status-idle",
            !isPending && !isError && "bg-status-success",
          )}
        />
        <span className="text-sm">
          {isPending && "Checking..."}
          {isError && "Unreachable"}
          {data && `spindle v${data.version} · up ${Math.floor(data.uptime_seconds)}s`}
        </span>
      </CardContent>
    </Card>
  );
}
