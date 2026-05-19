import { createFileRoute } from "@tanstack/react-router";
import { MessagesSquare } from "lucide-react";

// /sessions index — shown in the right pane when no session is selected.
// The list/search lives in the sessions layout route (route.tsx).
export const Route = createFileRoute("/sessions/")({
  component: SessionsEmptyState,
});

function SessionsEmptyState() {
  return (
    <div className="h-full flex flex-col items-center justify-center text-center text-muted-foreground p-8">
      <MessagesSquare className="h-8 w-8 mb-3 opacity-60" />
      <p className="text-sm font-medium text-foreground">No session selected</p>
      <p className="mt-1 text-xs">
        Pick a conversation on the left to view its transcript and tool trace.
      </p>
    </div>
  );
}
