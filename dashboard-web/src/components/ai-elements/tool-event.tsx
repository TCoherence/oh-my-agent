import {
  ChevronRight,
  Code2,
  FileEdit,
  Globe,
  HelpCircle,
  Search,
  Terminal,
  Wand2,
  FileText,
  FolderSearch,
  Bot,
  AlertTriangle,
  Coins,
  Sparkles,
} from "lucide-react";
import { type ReactNode, useState } from "react";

import { cn } from "@/lib/utils";
import type { TraceEvent } from "@/lib/api";

/**
 * Render one ``TraceEvent`` as a chain-of-thought step. Picks an icon
 * per tool name (matches the agentara design pattern). The whole row
 * is collapsible — click to expand the input / output / raw fields.
 *
 * The icon set covers Claude's built-in tools (Bash / Read / Edit / Grep /
 * WebFetch / WebSearch / Write / Glob) plus generic fallbacks for
 * unknown tool names (e.g. agent-created custom tools).
 */
const ICONS: Record<string, typeof Bot> = {
  Bash: Terminal,
  Read: FileText,
  Write: FileText,
  Edit: FileEdit,
  Grep: Search,
  Glob: FolderSearch,
  WebFetch: Globe,
  WebSearch: Globe,
  Agent: Bot,
  Skill: Sparkles,
};

function iconFor(name: string | undefined): typeof Bot {
  if (!name) return Code2;
  if (name in ICONS) return ICONS[name];
  return Wand2;
}

export function ToolEvent({ event }: { event: TraceEvent }) {
  const [open, setOpen] = useState(false);

  if (event.type === "thinking") {
    return (
      <Row icon={HelpCircle} label="thinking" subtle>
        <pre className="whitespace-pre-wrap text-xs text-muted-foreground">
          {event.text || ""}
        </pre>
      </Row>
    );
  }

  if (event.type === "usage") {
    return (
      <Row icon={Coins} label="usage" subtle>
        <div className="text-xs text-muted-foreground">
          input: {event.input_tokens ?? "?"} · output: {event.output_tokens ?? "?"}
          {typeof event.cost_usd === "number"
            ? ` · cost: $${event.cost_usd.toFixed(4)}`
            : ""}
        </div>
      </Row>
    );
  }

  if (event.type === "error") {
    return (
      <Row icon={AlertTriangle} label={`error${event.error_kind ? `: ${event.error_kind}` : ""}`} danger>
        <pre className="whitespace-pre-wrap text-xs">{event.message || ""}</pre>
      </Row>
    );
  }

  if (event.type === "tool_use") {
    const Icon = iconFor(event.name);
    const inputSummary = summarizeInput(event.input);
    return (
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full text-left"
      >
        <div
          className={cn(
            "flex items-start gap-3 rounded-md border border-border/50 bg-card/30 px-3 py-2",
            "hover:bg-accent/40 transition-colors",
          )}
        >
          <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 text-xs">
              <span className="font-medium">{event.name}</span>
              {inputSummary ? (
                <span className="text-muted-foreground truncate">{inputSummary}</span>
              ) : null}
            </div>
            {open ? (
              <pre className="mt-2 max-h-64 overflow-auto rounded bg-background/60 p-2 text-[11px]">
                {JSON.stringify(event.input ?? {}, null, 2)}
              </pre>
            ) : null}
          </div>
          <ChevronRight
            className={cn(
              "h-4 w-4 text-muted-foreground transition-transform",
              open && "rotate-90",
            )}
          />
        </div>
      </button>
    );
  }

  if (event.type === "tool_result") {
    return (
      <Row
        icon={event.is_error ? AlertTriangle : Code2}
        label={`result${event.name ? `: ${event.name}` : ""}`}
        danger={Boolean(event.is_error)}
        subtle
      >
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap text-[11px] text-muted-foreground">
          {(event.output || "").slice(0, 2000)}
        </pre>
      </Row>
    );
  }

  if (event.type === "system_init") {
    return (
      <Row icon={Sparkles} label="session" subtle>
        <div className="text-xs text-muted-foreground">
          {event.model ? `model: ${event.model}` : null}
          {event.tools?.length ? ` · tools: ${event.tools.join(", ")}` : null}
        </div>
      </Row>
    );
  }

  // Fallback for text / complete events the caller didn't pre-filter.
  return (
    <Row icon={Code2} label={event.type} subtle>
      <pre className="whitespace-pre-wrap text-xs text-muted-foreground">
        {event.text || JSON.stringify(event, null, 2)}
      </pre>
    </Row>
  );
}

function Row({
  icon: Icon,
  label,
  children,
  subtle,
  danger,
}: {
  icon: typeof Bot;
  label: string;
  children: ReactNode;
  subtle?: boolean;
  danger?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-md border px-3 py-2",
        subtle ? "border-border/40 bg-card/20" : "border-border bg-card",
        danger && "border-red-500/40 bg-red-500/10",
      )}
    >
      <Icon
        className={cn(
          "mt-0.5 h-4 w-4 shrink-0",
          danger ? "text-red-500" : "text-muted-foreground",
        )}
      />
      <div className="flex-1 min-w-0">
        <div className="text-xs font-medium text-foreground">{label}</div>
        <div className="mt-1">{children}</div>
      </div>
    </div>
  );
}

function summarizeInput(input: Record<string, unknown> | undefined): string {
  if (!input) return "";
  // Cheap one-liner: first non-empty string value in the input dict.
  // Good enough for the collapsed-row preview; user clicks to see full.
  for (const v of Object.values(input)) {
    if (typeof v === "string" && v) {
      return v.length > 80 ? `${v.slice(0, 77)}...` : v;
    }
  }
  return "";
}
