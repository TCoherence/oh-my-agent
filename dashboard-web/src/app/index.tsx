import { createFileRoute, redirect } from "@tanstack/react-router";

// `/` redirects to `/sessions`. The session list is the only landing
// page in the read-only MVP; future pages (memory CRUD, tasks, etc.)
// will pick a different default once they ship.
export const Route = createFileRoute("/")({
  beforeLoad: () => {
    throw redirect({ to: "/sessions" });
  },
});
