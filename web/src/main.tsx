import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "sonner";

import { TooltipProvider } from "@/components/ui/tooltip";
import { EventProvider } from "@/providers/EventProvider";
import { PageTitleProvider } from "@/providers/PageTitleProvider";
import { ThemeProvider } from "@/providers/ThemeProvider";
import { routeTree } from "@/routeTree.gen";

import "@/styles/globals.css";

const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const queryClient = new QueryClient();

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element not found");
}

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <EventProvider>
          <PageTitleProvider>
            <TooltipProvider delayDuration={200}>
              <RouterProvider router={router} />
              <Toaster
                position="bottom-right"
                toastOptions={{
                  classNames: {
                    toast: "bg-card! text-foreground! border-border!",
                    description: "text-muted-foreground!",
                  },
                }}
              />
            </TooltipProvider>
          </PageTitleProvider>
        </EventProvider>
      </ThemeProvider>
    </QueryClientProvider>
  </StrictMode>,
);
