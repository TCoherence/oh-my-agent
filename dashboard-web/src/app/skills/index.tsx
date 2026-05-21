import { createFileRoute } from "@tanstack/react-router";

import { Skeleton } from "@/components/ui/skeleton";
import { useSetSkillEnabled, useSkillHealth } from "@/hooks/use-skill-health";
import { ApiError, type SkillHealthRow } from "@/lib/api";
import { cn } from "@/lib/utils";

function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401)
      return 'auth required — set localStorage["oma-dashboard-token"]';
    if (err.status === 503)
      return "requires the co-located dashboard (runs inside the bot process)";
  }
  return (err as Error)?.message ?? "unknown error";
}

export const Route = createFileRoute("/skills/")({
  component: SkillsPage,
});

function fmtPct(n: number | null): string {
  if (n === null) return "—";
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

function SkillsPage() {
  const { data, isLoading, isError, error } = useSkillHealth();
  const toggle = useSetSkillEnabled();

  return (
    <div className="mx-auto max-w-4xl px-6 py-6">
      <h1 className="text-lg font-semibold mb-4">Skill health</h1>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : isError ? (
        <p className="text-sm text-destructive">
          Failed to load skill health: {describeError(error)}
        </p>
      ) : !data || data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No skill runs in the last 30 days.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-card text-muted-foreground text-xs">
              <tr>
                <th className="text-left px-3 py-2">Skill</th>
                <th className="text-right px-3 py-2">7d</th>
                <th className="text-right px-3 py-2">30d</th>
                <th className="text-right px-3 py-2">Success</th>
                <th className="text-right px-3 py-2">Neg. fb</th>
                <th className="text-left px-3 py-2">Last run</th>
                <th className="text-right px-3 py-2">State</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row: SkillHealthRow) => (
                <tr
                  key={row.skill}
                  className="border-t border-border hover:bg-accent/30"
                >
                  <td className="px-3 py-2 font-mono text-xs">
                    {row.skill}
                    {row.last_failure_reason ? (
                      <span
                        className="ml-2 text-destructive"
                        title={row.last_failure_reason}
                      >
                        ⚠
                      </span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {row.runs_7d}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {row.runs_30d}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {fmtPct(row.success_rate)}
                  </td>
                  <td
                    className={cn(
                      "px-3 py-2 text-right tabular-nums",
                      (row.negative_feedback_rate ?? 0) > 0.3
                        ? "text-destructive"
                        : "",
                    )}
                  >
                    {fmtPct(row.negative_feedback_rate)}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground text-xs">
                    {fmtRelative(row.last_run_at)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      type="button"
                      disabled={toggle.isPending}
                      onClick={() =>
                        toggle.mutate({ name: row.skill, enabled: row.disabled })
                      }
                      className={cn(
                        "px-2 py-1 rounded text-xs transition-colors",
                        row.disabled
                          ? "bg-destructive/15 text-destructive hover:bg-destructive/25"
                          : "bg-primary/10 text-primary hover:bg-primary/20",
                      )}
                    >
                      {row.disabled ? "disabled · enable" : "enabled · disable"}
                    </button>
                  </td>
                </tr>
              ))}
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
