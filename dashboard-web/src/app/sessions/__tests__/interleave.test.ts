import { describe, expect, it } from "vitest";

import { interleave } from "@/app/sessions/$platform.$channelId.$threadId";
import type { TraceEvent, TurnRow } from "@/lib/api";

function turn(id: number, createdAt: string): TurnRow {
  return {
    _id: id,
    role: "assistant",
    content: "hi",
    author: null,
    agent: "claude",
    created_at: createdAt,
  };
}

function ev(ts: string, type: TraceEvent["type"], toolId?: string): TraceEvent {
  return { ts, type, tool_id: toolId };
}

describe("interleave", () => {
  it("merge-sorts turns and tool events by timestamp", () => {
    const turns = [turn(1, "2026-05-12T10:00:00Z"), turn(2, "2026-05-12T10:02:00Z")];
    const events = [ev("2026-05-12T10:01:00Z", "tool_use", "tu_1")];
    const out = interleave(turns, events);
    expect(out.map((i) => i.kind)).toEqual(["turn", "tool", "turn"]);
  });

  it("filters complete/text events that duplicate the assistant turn", () => {
    const events = [
      ev("2026-05-12T10:01:00Z", "complete"),
      ev("2026-05-12T10:01:01Z", "text"),
      ev("2026-05-12T10:01:02Z", "thinking"),
    ];
    const out = interleave([], events);
    expect(out).toHaveLength(1);
    expect(out[0].kind).toBe("tool");
  });

  it("assigns keys that are stable across polls (no random fallback)", () => {
    // Two thinking events with identical ts and no tool_id — the old
    // Math.random() fallback gave them fresh keys every poll, remounting
    // each row every 2s. Keys must be deterministic AND unique.
    const events = [
      ev("2026-05-12T10:01:00Z", "thinking"),
      ev("2026-05-12T10:01:00Z", "thinking"),
      ev("2026-05-12T10:01:00Z", "tool_use", "tu_1"),
    ];
    const first = interleave([], events).map((i) => i.key);
    const second = interleave([], events).map((i) => i.key);
    expect(first).toEqual(second); // stable poll-to-poll
    expect(new Set(first).size).toBe(first.length); // unique among siblings
  });

  it("keeps existing keys unchanged when new events are appended", () => {
    const events = [
      ev("2026-05-12T10:01:00Z", "tool_use", "tu_1"),
      ev("2026-05-12T10:01:05Z", "tool_result", "tu_1"),
    ];
    const before = interleave([], events).map((i) => i.key);
    const after = interleave([], [...events, ev("2026-05-12T10:01:09Z", "usage")]).map(
      (i) => i.key,
    );
    expect(after.slice(0, before.length)).toEqual(before);
  });
});
