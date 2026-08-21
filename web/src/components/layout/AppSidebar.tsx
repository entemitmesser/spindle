import { useQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import {
  Disc3,
  FolderOpen,
  LayoutDashboard,
  type LucideIcon,
  Pin,
  PinOff,
  ScrollText,
  Settings,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import { useSidebar } from "@/providers/SidebarProvider";
import { cn } from "@/lib/utils";

const PIN_STORAGE_KEY = "spindle-sidebar-pinned";

interface NavItem {
  label: string;
  icon: LucideIcon;
  to: "/" | "/jobs" | "/library" | "/settings" | "/logs";
}

const NAV_ITEMS: NavItem[] = [
  { label: "Dashboard", icon: LayoutDashboard, to: "/" },
  { label: "Jobs", icon: Disc3, to: "/jobs" },
  { label: "Library", icon: FolderOpen, to: "/library" },
  { label: "Settings", icon: Settings, to: "/settings" },
  { label: "Logs", icon: ScrollText, to: "/logs" },
];

function NavLink({
  item,
  expanded,
  onNavigate,
}: {
  item: NavItem;
  expanded: boolean;
  onNavigate?: () => void;
}) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const isActive = item.to === "/" ? pathname === "/" : pathname.startsWith(item.to);
  const Icon = item.icon;

  const link = (
    <Link
      to={item.to}
      onClick={onNavigate}
      aria-label={item.label}
      aria-current={isActive ? "page" : undefined}
      className={cn(
        "text-muted-foreground hover:bg-muted hover:text-foreground flex h-10 items-center gap-3 rounded-lg px-2.5 transition-colors",
        isActive && "bg-primary/10 text-primary",
        expanded ? "w-full" : "w-10 justify-center",
      )}
    >
      <Icon className="size-5 shrink-0" />
      {expanded && <span className="truncate text-sm font-medium">{item.label}</span>}
    </Link>
  );

  if (expanded) return link;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{link}</TooltipTrigger>
      <TooltipContent side="right">{item.label}</TooltipContent>
    </Tooltip>
  );
}

function SidebarNav({ expanded, onNavigate }: { expanded: boolean; onNavigate?: () => void }) {
  return (
    <nav className={cn("flex flex-1 flex-col gap-1", expanded ? "px-3" : "items-center px-2")}>
      {NAV_ITEMS.map((item) => (
        <NavLink key={item.to} item={item} expanded={expanded} onNavigate={onNavigate} />
      ))}
    </nav>
  );
}

function VersionFooter({ expanded }: { expanded: boolean }) {
  const { data } = useQuery({ queryKey: queryKeys.system.info(), queryFn: api.system });
  if (!data || !expanded) return null;
  return <p className="text-muted-foreground px-3 pb-1 text-xs">v{data.version}</p>;
}

export function AppSidebar() {
  const [pinned, setPinned] = useState(() => {
    try {
      return localStorage.getItem(PIN_STORAGE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [hovering, setHovering] = useState(false);
  const { mobileOpen, setMobileOpen } = useSidebar();
  const expanded = pinned || hovering;

  useEffect(() => {
    try {
      localStorage.setItem(PIN_STORAGE_KEY, pinned ? "1" : "0");
    } catch {
      // ignore
    }
  }, [pinned]);

  return (
    <>
      <aside
        onMouseEnter={() => setHovering(true)}
        onMouseLeave={() => setHovering(false)}
        className={cn(
          "bg-card border-border sticky top-0 hidden h-screen shrink-0 flex-col gap-1 border-r py-4 transition-[width] duration-150 md:flex",
          expanded ? "w-56" : "w-14",
        )}
      >
        <SidebarNav expanded={expanded} />
        <VersionFooter expanded={expanded} />
        <div className={cn("flex", expanded ? "justify-end px-3" : "justify-center px-2")}>
          <button
            type="button"
            onClick={() => setPinned((p) => !p)}
            aria-label={pinned ? "Unpin sidebar" : "Pin sidebar expanded"}
            aria-pressed={pinned}
            title={pinned ? "Unpin sidebar" : "Pin sidebar expanded"}
            className="text-muted-foreground hover:bg-muted hover:text-foreground flex size-8 items-center justify-center rounded-lg transition-colors"
          >
            {pinned ? <PinOff className="size-4" /> : <Pin className="size-4" />}
          </button>
        </div>
      </aside>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="w-64 p-0">
          <SheetHeader>
            <SheetTitle>Spindle</SheetTitle>
          </SheetHeader>
          <div className="flex flex-1 flex-col py-2">
            <SidebarNav expanded onNavigate={() => setMobileOpen(false)} />
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
