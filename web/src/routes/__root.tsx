import { Outlet, createRootRoute } from "@tanstack/react-router";

import { AppShell } from "@/components/layout/AppShell";
import { AuthGate } from "@/providers/AuthGate";
import { EventProvider } from "@/providers/EventProvider";

export const Route = createRootRoute({
  component: RootComponent,
});

function RootComponent() {
  return (
    <AuthGate>
      <EventProvider>
        <AppShell>
          <Outlet />
        </AppShell>
      </EventProvider>
    </AuthGate>
  );
}
