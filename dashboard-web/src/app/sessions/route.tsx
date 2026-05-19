import { Link, Outlet, createFileRoute } from "@tanstack/react-router";
import { Search, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useSessions } from "@/hooks/use-sessions";
import { useSessionSearch } from "@/hooks/use-session-search";
import type { SessionSearchHit } from "@/lib/api";
import { cn, formatRelative } from "@/lib/utils";

// Layout route for /sessions: a persistent list/search pane on the left,
// the selected session rendered in the right pane via <Outlet/>. Clicking
// a session navigates a child route, so the list stays mounted (no
// refetch, no full-page jump).
export const Route = createFileRoute("/sessions")({
  component: SessionsLayout,
});

function SessionsLayout() {
  return (
    <div className="flex h-[calc(100vh-3.0625rem)]">
      <aside className="w-[22rem] shrink-0 border-r border-border overflow-y-auto">
        <ListPane />
      </aside>
      <main className="flex-1 min-w-0 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}

function ListPane() {
  const [input, setInput] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(input), 250);
    return () => clearTimeout(t);
  }, [input]);
  const searching = debounced.trim().length >= 2;

  return (
    <div className="p-3">
      <div className="relative mb-3">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Search messages…"
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
      {searching ? <SearchResults query={debounced} /> : <SessionRows />}
    </div>
  );
}

function SessionRows() {
  const { data, isLoading, isError, error } = useSessions({ limit: 100 });

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }
  if (isError) {
    return (
      <Card>
        <CardContent className="py-4 text-xs">
          <div className="text-red-500 font-medium">Failed to load</div>
          <div className="mt-1 text-muted-foreground">
            {(error as Error).message}
          </div>
        </CardContent>
      </Card>
    );
  }
  if (!data || data.items.length === 0) {
    return (
      <p className="px-1 py-8 text-center text-xs text-muted-foreground">
        No sessions yet — send a message in Discord.
      </p>
    );
  }
  return (
    <ul className="space-y-1">
      {data.items.map((s) => (
        <li key={`${s.platform}:${s.channel_id}:${s.thread_id}`}>
          <Link
            to="/sessions/$platform/$channelId/$threadId"
            params={{
              platform: s.platform,
              channelId: s.channel_id,
              threadId: s.thread_id,
            }}
            activeProps={{ className: "bg-accent/60 border-primary/40" }}
            className={cn(
              "block rounded-md border border-transparent hover:bg-accent/40",
              "transition-colors px-3 py-2",
            )}
          >
            <div className="text-xs font-medium truncate">
              <span className="text-muted-foreground">{s.platform}/</span>
              <span className="text-primary">{s.thread_id}</span>
            </div>
            <div className="text-[11px] text-muted-foreground mt-0.5 truncate">
              {s.turn_count} turn{s.turn_count === 1 ? "" : "s"} ·{" "}
              {s.last_role ?? "—"} · {formatRelative(s.last_turn_at)}
            </div>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function SearchResults({ query }: { query: string }) {
  const { data, isLoading, isError, error } = useSessionSearch(query);

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    );
  }
  if (isError) {
    return (
      <Card>
        <CardContent className="py-4 text-xs">
          <div className="text-red-500 font-medium">Search failed</div>
          <div className="mt-1 text-muted-foreground">
            {(error as Error).message}
          </div>
        </CardContent>
      </Card>
    );
  }
  if (!data || data.items.length === 0) {
    return (
      <p className="px-1 py-8 text-center text-xs text-muted-foreground">
        No matches for <span className="text-foreground">"{query}"</span>.
      </p>
    );
  }
  return (
    <>
      <div className="text-[11px] text-muted-foreground mb-1.5 px-1">
        {data.items.length} match{data.items.length === 1 ? "" : "es"}
      </div>
      <ul className="space-y-1">
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
      activeProps={{ className: "bg-accent/60 border-primary/40" }}
      className={cn(
        "block rounded-md border border-transparent hover:bg-accent/40",
        "transition-colors px-3 py-2",
      )}
    >
      <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
        <span className="truncate">
          <span className="text-primary">{hit.thread_id}</span>
        </span>
        <span className="shrink-0">
          {hit.role} · {formatRelative(hit.created_at)}
        </span>
      </div>
      <div className="mt-1 text-xs line-clamp-2 break-words">{hit.snippet}</div>
    </Link>
  );
}
