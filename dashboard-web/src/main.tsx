import "@/global.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { routeTree } from "@/routeTree.gen";

// One shared QueryClient. Polling-first design (per the dashboard plan):
// each page sets its own `refetchInterval` instead of one global tick so
// the chat page can poll fast (2s) without forcing the sessions list to
// hammer the API at the same rate.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Treat data as stale immediately so refetch on focus / mount /
      // interval works as expected. Without this, the 5-min default
      // would silently mask backend updates while the tab is open.
      staleTime: 0,
      // Network errors are transient (FastAPI restarts during dev,
      // brief Tailscale hiccups, etc.). Try once with a 1.5s backoff;
      // beyond that it's not transient and we should surface the error.
      retry: 1,
      retryDelay: 1500,
    },
  },
});

const router = createRouter({
  routeTree,
  defaultPreload: "intent",
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("#root element missing from index.html");
}

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
