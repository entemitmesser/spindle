import { Link } from "@tanstack/react-router";
import { Disc3, FolderOpen, LayoutDashboard, ScrollText, Settings } from "lucide-react";
import type { LucideIcon } from "lucide-react";

interface NavItem {
  label: string;
  icon: LucideIcon;
  to?: "/";
}

const NAV_ITEMS: NavItem[] = [
  { label: "Dashboard", icon: LayoutDashboard, to: "/" },
  { label: "Jobs", icon: Disc3 },
  { label: "Library", icon: FolderOpen },
  { label: "Settings", icon: Settings },
  { label: "Logs", icon: ScrollText },
];

export function Sidebar() {
  return (
    <aside className="bg-card border-border flex w-14 flex-col items-center gap-1 border-r py-4">
      {NAV_ITEMS.map(({ label, icon: Icon, to }) =>
        to ? (
          <Link
            key={label}
            to={to}
            aria-label={label}
            title={label}
            className="text-muted-foreground hover:bg-muted hover:text-foreground [&.active]:bg-primary/10 [&.active]:text-primary flex h-10 w-10 items-center justify-center rounded-lg transition-colors"
          >
            <Icon className="h-5 w-5" />
          </Link>
        ) : (
          <span
            key={label}
            aria-label={`${label} (coming soon)`}
            title={`${label} (coming soon)`}
            className="text-muted-foreground/40 flex h-10 w-10 cursor-not-allowed items-center justify-center rounded-lg"
          >
            <Icon className="h-5 w-5" />
          </span>
        ),
      )}
    </aside>
  );
}
