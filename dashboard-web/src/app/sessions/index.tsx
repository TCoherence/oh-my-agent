import { Link, createFileRoute } from "@tanstack/react-router";
import { ChevronRight, Search, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useSessions } from "@/hooks/use-sessions";
import { useSessionSearch } from "@/hooks/use-session-search";
import type { SessionSearchHit } from "@/lib/api";
import { cn, formatRelative } from "@/lib/utils";

export const Route = createFileRoute("/sessions/")({
  component: SessionsListPage,
});

function SessionsListPage() {
  const [input, setInput] = useState("");
  const [debounced, setDebounced] = useState("");

  // Debounce so each keystroke doesn't fire a MATCH query.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(input), 250);
    return () => clearTimeout(t);
  }, [input]);

  const searching = debounced.trim().length >= 2;

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <h1 className="text-lg font-semibold mb-4">Sessions</h1>

      <div className="relative mb-4">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Search messages across all sessions…"
          className={cn(
            "w-full rounded-md border border-border bg-card pl-9 pr-9 py-2",
            "text-sm outline-none focus:border-primary/60 transition-colors",
          )}
        />
        {input ? (
          <button
            type="button"
            onClick={() => setInput("")}
            aria-label="Clear search"
            className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        ) : null}
      </div>

      {searching ? (
        <SearchResults query={debounced} />
      ) : (
        <SessionList />
      )}
    </div>
  );
}

function SessionList() {
  const { data, isLoading, isError, error } = useSessions({ limit: 100 });

  return (
    <>
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
    </>
  );
}

function SearchResults({ query }: { query: string }) {
  const { data, isLoading, isError, error } = useSessionSearch(query);

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <Card>
        <CardContent className="py-6 text-sm">
          <div className="text-red-500 font-medium">Search failed</div>
          <div className="mt-2 text-xs text-muted-foreground">
            {(error as Error).message}
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!data || data.items.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center text-muted-foreground text-sm">
          No messages match <span className="text-foreground">"{query}"</span>.
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      <div className="text-xs text-muted-foreground mb-2">
        {data.items.length} match{data.items.length === 1 ? "" : "es"}
      </div>
      <ul className="space-y-2">
        {data.items.map((h) => (
          <li key={`${h.platform}:${h.channel_id}:${h.thread_id}:${h._id}`}>
            <SearchHitRow hit={h} />
          </li>
        ))}
      </ul>
    </>
  );
}

function SearchHitRow({ hit }: { hit: SessionSearchHit }) {
  return (
    <Link
      to="/sessions/$platform/$channelId/$threadId"
      params={{
        platform: hit.platform,
        channelId: hit.channel_id,
        threadId: hit.thread_id,
      }}
      className={cn(
        "block rounded-md border border-border bg-card hover:bg-accent/40",
        "transition-colors px-4 py-3",
      )}
    >
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="truncate font-mono">
          {hit.platform}/{hit.channel_id}/
          <span className="text-primary">{hit.thread_id}</span>
        </span>
        <span className="shrink-0">
          {hit.role} · {formatRelative(hit.created_at)}
        </span>
      </div>
      <div className="mt-1 text-sm line-clamp-2 break-words">{hit.snippet}</div>
    </Link>
  );
}
