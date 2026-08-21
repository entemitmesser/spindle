import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { HardDrive, Menu, Moon, Settings, Sun, WifiOff } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { queryKeys } from "@/lib/query-keys";
import { useEventStream } from "@/providers/EventProvider";
import { useCurrentPageTitle } from "@/providers/PageTitleProvider";
import { useSidebar } from "@/providers/SidebarProvider";
import { useTheme } from "@/providers/ThemeProvider";

function DiskSpacePill() {
  const { data } = useQuery({
    queryKey: queryKeys.system.info(),
    queryFn: api.system,
    refetchInterval: 30000,
  });
  const primary = data?.disk[0];
  if (!primary) return null;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="text-muted-foreground hidden items-center gap-1.5 text-xs sm:flex">
          <HardDrive className="size-3.5" aria-hidden="true" />
          <span>{formatBytes(primary.free_bytes)} free</span>
        </span>
      </TooltipTrigger>
      <TooltipContent>
        {primary.label}: {formatBytes(primary.free_bytes)} free of{" "}
        {formatBytes(primary.total_bytes)}
      </TooltipContent>
    </Tooltip>
  );
}

function ConnectionPill() {
  const { connected } = useEventStream();
  if (connected) return null;

  return (
    <span className="border-status-warning/40 bg-status-warning/10 text-status-warning inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium">
      <WifiOff className="size-3.5" aria-hidden="true" />
      Reconnecting…
    </span>
  );
}

function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={toggleTheme}
          aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        >
          {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{theme === "dark" ? "Light theme" : "Dark theme"}</TooltipContent>
    </Tooltip>
  );
}

export function AppHeader() {
  const title = useCurrentPageTitle();
  const { setMobileOpen } = useSidebar();

  return (
    <header className="bg-background/95 border-border sticky top-0 z-10 flex h-14 items-center gap-3 border-b px-4 backdrop-blur-sm md:px-6">
      <Button
        variant="ghost"
        size="icon-sm"
        className="md:hidden"
        aria-label="Open navigation"
        onClick={() => setMobileOpen(true)}
      >
        <Menu className="size-5" />
      </Button>
      <h1 className="truncate text-2xl font-semibold">{title}</h1>
      <div className="ml-auto flex items-center gap-3">
        <DiskSpacePill />
        <ConnectionPill />
        <ThemeToggle />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon-sm" aria-label="Settings" asChild>
              <Link to="/settings">
                <Settings className="size-4" />
              </Link>
            </Button>
          </TooltipTrigger>
          <TooltipContent>Settings</TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}
