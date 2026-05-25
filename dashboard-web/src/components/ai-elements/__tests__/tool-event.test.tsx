import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ToolEvent } from "@/components/ai-elements/tool-event";
import type { TraceEvent } from "@/lib/api";

afterEach(() => {
  cleanup();
});

describe("ToolEvent", () => {
  it("renders a tool_use row and toggles the raw input on click", () => {
    const event: TraceEvent = {
      ts: "2026-01-01T00:00:00Z",
      type: "tool_use",
      name: "Bash",
      input: { command: "ls -la" },
    };
    render(<ToolEvent event={event} />);

    expect(screen.getByText("Bash")).toBeInTheDocument();
    // Collapsed: the JSON body (with the "command" key) is not rendered.
    expect(screen.queryByText(/"command"/)).toBeNull();

    fireEvent.click(screen.getByRole("button"));
    // Expanded: the raw input JSON is shown.
    expect(screen.getByText(/"command": "ls -la"/)).toBeInTheDocument();
  });

  it("renders a thinking row with its text", () => {
    const event: TraceEvent = {
      ts: "2026-01-01T00:00:00Z",
      type: "thinking",
      text: "pondering the answer",
    };
    render(<ToolEvent event={event} />);
    expect(screen.getByText("thinking")).toBeInTheDocument();
    expect(screen.getByText("pondering the answer")).toBeInTheDocument();
  });
});
