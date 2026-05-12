import { Link, createFileRoute } from "@tanstack/react-router";
import { ChevronLeft } from "lucide-react";
import { useMemo } from "react";

import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
} from "@/components/ai-elements/conversation";
import { Message, MessageContent, MessageMeta } from "@/components/ai-elements/message";
import { ToolEvent } from "@/components/ai-elements/tool-event";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useSessionHistory } from "@/hooks/use-session-history";
import { useSessionTrace } from "@/hooks/use-session-trace";
import type { TraceEvent, TurnRow } from "@/lib/api";
import { formatLocal, todayYmd } from "@/lib/utils";

export const Route = createFileRoute(
  "/sessions/$platform/$channelId/$threadId",
)({
  component: SessionDetailPage,
});

function SessionDetailPage() {
  const { platform, channelId, threadId } = Route.useParams();
  const date = todayYmd();

  const history = useSessionHistory({ platform, channelId, threadId, limit: 200 });
  const trace = useSessionTrace({ platform, channelId, threadId, date });

  // Interleave assistant turns with the tool events that fired between
  // them. Strategy: for each assistant turn, attach all trace events
  // whose timestamp is between the previous turn's created_at and the
  // current turn's created_at. user / system turns have no events.
  const interleaved = useMemo(
    () => interleave(history.data ?? [], trace.data?.items ?? []),
    [history.data, trace.data],
  );

  return (
    <div className="flex flex-col h-[calc(100vh-3rem)]">
      <div className="border-b border-border px-6 py-3 flex items-center gap-3">
        <Link
          to="/sessions"
          className="text-muted-foreground hover:text-foreground transition-colors"
        >
          <ChevronLeft className="h-4 w-4" />
        </Link>
        <div className="text-sm font-mono">
          <span className="text-muted-foreground">{platform}/{channelId}/</span>
          <span className="text-foreground">{threadId}</span>
        </div>
        <div className="ml-auto text-xs text-muted-foreground">
          {trace.data && !trace.data.enabled ? (
            <span title="enable experiment.tool_trace in config.yaml">
              tool trace disabled
            </span>
          ) : (
            <span>auto-refresh 2s · trace: {date}</span>
          )}
        </div>
      </div>

      <Conversation className="flex-1 min-h-0">
        <ConversationContent>
          {history.isLoading ? <LoadingMessages /> : null}

          {history.isError ? (
            <Card className="border-red-500/40 bg-red-500/10 p-4 text-sm">
              <div className="font-medium text-red-500">
                Failed to load history
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {(history.error as Error).message}
              </div>
            </Card>
          ) : null}

          {history.data && history.data.length === 0 ? (
            <ConversationEmptyState
              title="No turns in this thread yet"
              description="Send a message to start the conversation"
            />
          ) : null}

          {interleaved.map((item) => {
            if (item.kind === "turn") {
              return <TurnView key={`turn-${item.row._id}`} row={item.row} />;
            }
            return (
              <div
                key={`tool-${item.event.ts}-${item.event.tool_id ?? Math.random()}`}
                className="ml-12 my-1"
              >
                <ToolEvent event={item.event} />
              </div>
            );
          })}
        </ConversationContent>
      </Conversation>
    </div>
  );
}

function LoadingMessages() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton
          key={i}
          className={i % 2 ? "h-14 w-2/3 ml-auto" : "h-14 w-2/3"}
        />
      ))}
    </div>
  );
}

function TurnView({ row }: { row: TurnRow }) {
  return (
    <Message role={row.role}>
      <MessageContent>{row.content}</MessageContent>
      <MessageMeta>
        {row.role === "assistant" && row.agent ? `${row.agent} · ` : ""}
        {row.role === "user" && row.author ? `${row.author} · ` : ""}
        {formatLocal(row.created_at)}
      </MessageMeta>
    </Message>
  );
}

type InterleavedItem =
  | { kind: "turn"; row: TurnRow; ts: number }
  | { kind: "tool"; event: TraceEvent; ts: number };

function interleave(history: TurnRow[], events: TraceEvent[]): InterleavedItem[] {
  // Convert both lists to (ts_ms, item) tuples, then merge sort.
  // ``created_at`` from SQLite is ISO without explicit Z but stored UTC;
  // ``ts`` from trace JSONL is ISO with timezone. Date() parses both
  // formats reasonably — when ambiguous, we still get monotonic
  // ordering within one source which is what matters here.
  const out: InterleavedItem[] = [];
  for (const row of history) {
    out.push({ kind: "turn", row, ts: tsToMs(row.created_at) });
  }
  for (const ev of events) {
    // Skip "complete" / "text" events because their content typically
    // duplicates the assistant turn that comes through history. Keep
    // tool_use / tool_result / thinking / usage / error / system_init.
    if (ev.type === "complete" || ev.type === "text") continue;
    out.push({ kind: "tool", event: ev, ts: tsToMs(ev.ts) });
  }
  out.sort((a, b) => a.ts - b.ts);
  return out;
}

function tsToMs(iso: string): number {
  const n = Date.parse(iso);
  return Number.isNaN(n) ? 0 : n;
}
