import { Link, createFileRoute } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useSessions } from "@/hooks/use-sessions";
import { cn, formatRelative } from "@/lib/utils";

export const Route = createFileRoute("/sessions/")({
  component: SessionsListPage,
});

function SessionsListPage() {
  const { data, isLoading, isError, error } = useSessions({ limit: 100 });

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <h1 className="text-lg font-semibold mb-4">Sessions</h1>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : null}

      {isError ? (
        <Card>
          <CardContent className="py-6 text-sm text-muted-foreground">
            <div className="text-red-500 font-medium">Failed to load sessions</div>
            <div className="mt-2 text-xs">{(error as Error).message}</div>
          </CardContent>
        </Card>
      ) : null}

      {data && data.items.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground text-sm">
            <p>No sessions yet.</p>
            <p className="mt-2 text-xs">
              Send a message in Discord to start one — it will appear here within
              ~5s.
            </p>
          </CardContent>
        </Card>
      ) : null}

      <ul className="space-y-2">
        {data?.items.map((s) => (
          <li key={`${s.platform}:${s.channel_id}:${s.thread_id}`}>
            <Link
              to="/sessions/$platform/$channelId/$threadId"
              params={{
                platform: s.platform,
                channelId: s.channel_id,
                threadId: s.thread_id,
              }}
              className={cn(
                "block rounded-md border border-border bg-card hover:bg-accent/40",
                "transition-colors px-4 py-3",
              )}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">
                    {s.platform}/{s.channel_id}/<span className="text-primary">{s.thread_id}</span>
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {s.turn_count} turn{s.turn_count === 1 ? "" : "s"} · last{" "}
                    {s.last_role ?? "—"} · {formatRelative(s.last_turn_at)}
                  </div>
                </div>
                <ChevronRight className="h-4 w-4 text-muted-foreground" />
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
