import type { ReactNode } from "react";

interface SettingsSectionProps {
  label: string;
  description?: ReactNode;
  children: ReactNode;
}

/** Label / description / control row, reused across every settings tab
 * (ui-design.md §4). */
export function SettingsSection({ label, description, children }: SettingsSectionProps) {
  return (
    <div className="grid grid-cols-1 gap-3 border-b py-4 last:border-b-0 sm:grid-cols-[minmax(0,260px)_1fr] sm:gap-6">
      <div className="space-y-1">
        <p className="text-sm font-medium">{label}</p>
        {description && <p className="text-muted-foreground text-sm">{description}</p>}
      </div>
      <div className="max-w-md">{children}</div>
    </div>
  );
}
