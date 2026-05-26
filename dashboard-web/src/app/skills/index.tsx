import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import {
  useSetSkillEnabled,
  useSkillRecentTasks,
  useSkillsOverview,
} from "@/hooks/use-skill-health";
import { ApiError, type SkillOverviewRow } from "@/lib/api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/skills/")({
  component: SkillsPage,
});

// ── small helpers ───────────────────────────────────────────────────── //

function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401)
      return 'auth required — set localStorage["oma-dashboard-token"]';
    if (err.status === 503)
      return "requires the co-located dashboard (runs inside the bot process)";
  }
  return (err as Error)?.message ?? "unknown error";
}

function fmtPct(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `${Math.round(n * 100)}%`;
}

function fmtRelative(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const secs = (Date.now() - d.getTime()) / 1000;
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function successRateClass(rate: number | null): string {
  if (rate === null) return "text-muted-foreground";
  if (rate >= 0.9) return "text-emerald-600 dark:text-emerald-400";
  if (rate >= 0.7) return "text-amber-600 dark:text-amber-400";
  return "text-destructive";
}

type StateKind = "active" | "manual" | "auto" | "never-run" | "history-only";

function rowState(row: SkillOverviewRow): StateKind {
  if (!row.installed) return "history-only";
  if (row.disabled_kind === "manual") return "manual";
  if (row.disabled_kind === "auto") return "auto";
  if (row.runs_30d === 0) return "never-run";
  return "active";
}

const STATE_LABEL: Record<StateKind, string> = {
  active: "active",
  manual: "manually disabled",
  auto: "auto-disabled",
  "never-run": "never run",
  "history-only": "history only",
};

const STATE_HELP: Record<StateKind, string> = {
  active: "Skill is enabled and has runs in the last 30 days.",
  manual:
    "An operator turned this off via /skill_enable, the API, or this page. " +
    "Click the toggle to re-enable.",
  auto:
    "The bot disabled this skill itself after repeated failures. " +
    "Inspect last_failure_reason and re-enable when fixed.",
  "never-run":
    "Installed (SKILL.md exists) but has not been invoked in the last 30 days. " +
    "Either no traffic has triggered it, or its router intent is too narrow.",
  "history-only":
    "Has runs in runtime_tasks but no SKILL.md on disk — likely renamed " +
    "or deleted. Kept visible for audit; the bot can no longer execute it.",
};

function stateBadgeClass(s: StateKind): string {
  switch (s) {
    case "active":
      return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300";
    case "manual":
      return "bg-destructive/15 text-destructive";
    case "auto":
      return "bg-amber-500/15 text-amber-700 dark:text-amber-300";
    case "never-run":
      return "bg-muted text-muted-foreground";
    case "history-only":
      return "bg-purple-500/15 text-purple-700 dark:text-purple-300";
  }
}

// ── sorting ─────────────────────────────────────────────────────────── //

type SortKey =
  | "smart" // default: failing/disabled first, then by runs_30d desc
  | "name"
  | "runs_30d"
  | "success_rate"
  | "last_run_at"
  | "state";

const SORT_LABEL: Record<SortKey, string> = {
  smart: "Issues first",
  name: "Name (A→Z)",
  runs_30d: "Runs (30d, most)",
  success_rate: "Success rate (worst)",
  last_run_at: "Last run (newest)",
  state: "State",
};

const STATE_ORDER: Record<StateKind, number> = {
  manual: 0,
  auto: 1,
  "history-only": 2,
  active: 3,
  "never-run": 4,
};

function sortRows(rows: SkillOverviewRow[], key: SortKey): SkillOverviewRow[] {
  const copy = [...rows];
  copy.sort((a, b) => {
    if (key === "name") return a.skill.localeCompare(b.skill);
    if (key === "runs_30d") return b.runs_30d - a.runs_30d;
    if (key === "success_rate") {
      // null (no data) sorts last; lowest rate first.
      const ar = a.success_rate ?? 2;
      const br = b.success_rate ?? 2;
      return ar - br;
    }
    if (key === "last_run_at") {
      const at = a.last_run_at ? new Date(a.last_run_at).getTime() : 0;
      const bt = b.last_run_at ? new Date(b.last_run_at).getTime() : 0;
      return bt - at;
    }
    if (key === "state")
      return STATE_ORDER[rowState(a)] - STATE_ORDER[rowState(b)];
    // smart: state weight first, then failing skills, then by activity
    const aw = STATE_ORDER[rowState(a)];
    const bw = STATE_ORDER[rowState(b)];
    if (aw !== bw) return aw - bw;
    const ar = a.success_rate ?? 2;
    const br = b.success_rate ?? 2;
    if (ar !== br) return ar - br;
    return b.runs_30d - a.runs_30d;
  });
  return copy;
}

// ── component ────────────────────────────────────────────────────────── //

function SkillsPage() {
  const { data, isError, error } = useSkillsOverview();
  const toggle = useSetSkillEnabled();
  const [sortKey, setSortKey] = useState<SortKey>("smart");
  const [expanded, setExpanded] = useState<string | null>(null);

  const sorted = useMemo(
    () => (data ? sortRows(data.items, sortKey) : []),
    [data, sortKey],
  );

  // Skeleton ONLY on first load — once we have data OR an error, never
  // flash it again on the 5s background polls (same bug as automations).
  const showSkeleton = data === undefined && !isError;

  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
      <div className="mb-1 flex items-baseline justify-between">
        <h1 className="text-lg font-semibold">Skills</h1>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <label htmlFor="sort">sort</label>
          <select
            id="sort"
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="rounded border border-border bg-background px-2 py-1 text-xs"
          >
            {(Object.keys(SORT_LABEL) as SortKey[]).map((k) => (
              <option key={k} value={k}>
                {SORT_LABEL[k]}
              </option>
            ))}
          </select>
        </div>
      </div>
      <p className="mb-4 text-xs text-muted-foreground">
        Installed catalog from <code>skills/</code> (SKILL.md frontmatter),
        merged with runtime stats from <code>runtime_tasks</code> (last 30
        days). Click a row to drill into recent runs.
      </p>

      {data?.warnings && data.warnings.length > 0 ? (
        <div className="mb-3 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          {data.warnings.map((w, i) => (
            <div key={i}>⚠ {w}</div>
          ))}
        </div>
      ) : null}

      {showSkeleton ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : isError ? (
        <p className="text-sm text-destructive">
          Failed to load skills: {describeError(error)}
        </p>
      ) : sorted.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No skills installed or run in the last 30 days.
        </p>
      ) : (
        <div className="overflow-hidden rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-card text-muted-foreground text-xs">
              <tr>
                <th className="px-3 py-2 text-left w-[36%]">Skill</th>
                <th className="px-3 py-2 text-left">State</th>
                <th className="px-3 py-2 text-right">30d</th>
                <th className="px-3 py-2 text-right">Success</th>
                <th className="px-3 py-2 text-right">Neg.fb</th>
                <th className="px-3 py-2 text-left">Last run</th>
                <th className="px-3 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => {
                const isOpen = expanded === row.skill;
                const state = rowState(row);
                return (
                  <SkillRowGroup
                    key={row.skill}
                    row={row}
                    state={state}
                    isOpen={isOpen}
                    onToggleExpand={() =>
                      setExpanded(isOpen ? null : row.skill)
                    }
                    onToggleEnabled={() => {
                      // disabled_kind === "auto" or "manual" → enable. Else disable.
                      const willEnable = row.disabled_kind !== null;
                      toggle.mutate({ name: row.skill, enabled: willEnable });
                    }}
                    toggling={toggle.isPending}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {toggle.isError ? (
        <p className="mt-3 text-xs text-destructive">
          Toggle failed: {(toggle.error as Error)?.message}. A token may be
          required — set localStorage["oma-dashboard-token"].
        </p>
      ) : null}
    </div>
  );
}

// ── per-row group: summary + expandable detail panel ───────────────── //

interface RowProps {
  row: SkillOverviewRow;
  state: StateKind;
  isOpen: boolean;
  onToggleExpand: () => void;
  onToggleEnabled: () => void;
  toggling: boolean;
}

function SkillRowGroup({
  row,
  state,
  isOpen,
  onToggleExpand,
  onToggleEnabled,
  toggling,
}: RowProps) {
  return (
    <>
      <tr
        className={cn(
          "border-t border-border cursor-pointer hover:bg-accent/30",
          isOpen && "bg-accent/20",
        )}
        onClick={onToggleExpand}
      >
        <td className="px-3 py-2 align-top">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-xs">
              {isOpen ? "▾ " : "▸ "}
              {row.skill}
            </span>
            {!row.installed ? (
              <span className="text-[10px] uppercase tracking-wide text-purple-600 dark:text-purple-400">
                history only
              </span>
            ) : null}
          </div>
          {row.description ? (
            <div
              className="mt-0.5 line-clamp-1 text-xs text-muted-foreground"
              title={row.description}
            >
              {row.description}
            </div>
          ) : null}
        </td>
        <td className="px-3 py-2 align-top">
          <span
            title={STATE_HELP[state]}
            className={cn(
              "inline-block rounded px-1.5 py-0.5 text-[10px] font-medium",
              stateBadgeClass(state),
            )}
          >
            {STATE_LABEL[state]}
          </span>
        </td>
        <td className="px-3 py-2 text-right align-top tabular-nums">
          {row.runs_30d}
          {row.runs_7d ? (
            <div className="text-[10px] text-muted-foreground">
              {row.runs_7d} in 7d
            </div>
          ) : null}
        </td>
        <td
          className={cn(
            "px-3 py-2 text-right align-top tabular-nums",
            successRateClass(row.success_rate),
          )}
        >
          {fmtPct(row.success_rate)}
        </td>
        <td
          className={cn(
            "px-3 py-2 text-right align-top tabular-nums",
            (row.negative_feedback_rate ?? 0) > 0.3
              ? "text-destructive"
              : "text-muted-foreground",
          )}
        >
          {fmtPct(row.negative_feedback_rate)}
        </td>
        <td
          className="px-3 py-2 align-top text-xs text-muted-foreground"
          title={row.last_run_at ?? "no runs recorded"}
        >
          {fmtRelative(row.last_run_at)}
          {row.last_failure_reason ? (
            <div
              className="mt-0.5 truncate text-destructive"
              title={row.last_failure_reason}
            >
              ⚠ last failure
            </div>
          ) : null}
        </td>
        <td className="px-3 py-2 text-right align-top">
          {row.installed ? (
            <button
              type="button"
              disabled={toggling}
              onClick={(e) => {
                e.stopPropagation();
                onToggleEnabled();
              }}
              className={cn(
                "rounded px-2 py-1 text-xs transition-colors",
                row.disabled_kind !== null
                  ? "bg-primary/10 text-primary hover:bg-primary/20"
                  : "bg-destructive/15 text-destructive hover:bg-destructive/25",
              )}
              title={
                row.disabled_kind !== null
                  ? "Click to re-enable (writes a manual override)"
                  : "Click to manually disable"
              }
            >
              {row.disabled_kind !== null ? "enable" : "disable"}
            </button>
          ) : (
            <span className="text-[10px] text-muted-foreground">—</span>
          )}
        </td>
      </tr>
      {isOpen ? (
        <tr className="border-t border-border bg-card/40">
          <td colSpan={7} className="px-4 py-3">
            <SkillExpanded row={row} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function SkillExpanded({ row }: { row: SkillOverviewRow }) {
  const recent = useSkillRecentTasks(row.skill, { limit: 10 });
  return (
    <div className="space-y-3 text-xs">
      {row.description ? (
        <p className="leading-relaxed">{row.description}</p>
      ) : (
        <p className="text-muted-foreground">No description in SKILL.md.</p>
      )}

      <div className="flex flex-wrap gap-x-6 gap-y-1 text-muted-foreground">
        <span>
          <span className="text-foreground">{row.allowed_tool_count}</span>{" "}
          allowed tools
        </span>
        <span>
          timeout{" "}
          <span className="text-foreground">
            {row.timeout_seconds ? `${row.timeout_seconds}s` : "default"}
          </span>
        </span>
        <span>
          max_turns{" "}
          <span className="text-foreground">{row.max_turns ?? "default"}</span>
        </span>
      </div>

      {row.last_failure_reason ? (
        <details className="rounded border border-destructive/30 bg-destructive/5 p-2">
          <summary className="cursor-pointer text-destructive">
            Last failure reason
          </summary>
          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all text-[11px]">
            {row.last_failure_reason}
          </pre>
        </details>
      ) : null}

      <div>
        <div className="mb-1 font-medium text-foreground">Recent runs</div>
        {recent.isLoading && !recent.data ? (
          <div className="text-muted-foreground">loading…</div>
        ) : recent.isError ? (
          <div className="text-destructive">
            {describeError(recent.error)}
          </div>
        ) : !recent.data || recent.data.items.length === 0 ? (
          <div className="text-muted-foreground">no runs recorded</div>
        ) : (
          <ul className="divide-y divide-border rounded border border-border">
            {recent.data.items.map((t) => (
              <li key={t.id} className="grid grid-cols-[auto_auto_1fr] gap-3 px-2 py-1">
                <span className="text-muted-foreground">
                  {fmtRelative(t.at)}
                </span>
                <span
                  className={cn(
                    "font-mono text-[10px]",
                    t.status === "COMPLETED" || t.status === "MERGED"
                      ? "text-emerald-600 dark:text-emerald-400"
                      : t.status === "FAILED" ||
                        t.status === "TIMEOUT" ||
                        t.status === "CANCELLED"
                      ? "text-destructive"
                      : "text-muted-foreground",
                  )}
                >
                  {t.status}
                </span>
                <span className="truncate" title={t.goal}>
                  {t.goal}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
