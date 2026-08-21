import type { PropsWithChildren } from "react";

import { AppHeader } from "@/components/layout/AppHeader";
import { AppSidebar } from "@/components/layout/AppSidebar";
import { SystemWarningsBanner } from "@/components/layout/SystemWarningsBanner";
import { SidebarProvider } from "@/providers/SidebarProvider";

export function AppShell({ children }: PropsWithChildren) {
  return (
    <SidebarProvider>
      <div className="flex min-h-screen">
        <AppSidebar />
        <div className="flex flex-1 flex-col">
          <AppHeader />
          <SystemWarningsBanner />
          <main className="mx-auto w-full max-w-[1400px] flex-1 space-y-6 p-4 md:p-6">
            {children}
          </main>
        </div>
      </div>
    </SidebarProvider>
  );
}
