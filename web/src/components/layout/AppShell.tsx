import type { PropsWithChildren } from "react";

import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";

export function AppShell({ children }: PropsWithChildren) {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-y-auto">
        <Header />
        <main className="mx-auto w-full max-w-[1400px] flex-1 space-y-6 p-4 md:p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
