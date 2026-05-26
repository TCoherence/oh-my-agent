import { createFileRoute } from "@tanstack/react-router";

import { Skeleton } from "@/components/ui/skeleton";
import {
  useAutomations,
  useFireAutomation,
  usePatchAutomation,
} from "@/hooks/use-automations";
import type { AutomationRow } from "@/lib/api";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

function describeError(err: unknown): { title: string; body?: string } {
  if (err instanceof ApiError) {
    if (err.status === 401)
      return { title: 'auth required — set localStorage["oma-dashboard-token"]' };
    if (err.status === 503)
      return {
        title: "Automation control unavailable in this dashboard",
        body:
          "You're viewing the read-only standalone dashboard. Automation control (list, fire, pause) only works when the dashboard is co-located inside the bot process. Set `dashboard.colocated: true` in config.yaml (default port :8765) to manage automations.",
      };
  }
  return { title: (err as Error)?.message ?? "unknown error" };
}

export const Route = createFileRoute("/automations/")({
  component: AutomationsPage,
});

function fmtSchedule(row: AutomationRow): string {
  if (row.schedule_kind === "cron") return row.cron ?? "cron";
  if (row.interval_seconds) return `every ${row.interval_seconds}s`;
  return "—";
}

function fmtNext(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const secs = (d.getTime() - Date.now()) / 1000;
  if (secs < 0) return "due";
  if (secs < 60) return `in ${Math.floor(secs)}s`;
  if (secs < 3600) return `in ${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `in ${Math.floor(secs / 3600)}h`;
  return `in ${Math.floor(secs / 86400)}d`;
}

function AutomationsPage() {
  const { data, isError, error } = useAutomations();
  const fire = useFireAutomation();
  const patch = usePatchAutomation();

  const mutating = fire.isPending || patch.isPending;
  // Skeleton only on the first-ever load (no data, no error). Once either
  // settles, we keep the error/data view stable so background polls (every
  // 5s) don't repaint the skeleton — the bug that made the page look like
  // it was constantly reloading.
  const showSkeleton = data === undefined && !isError;
  const errInfo = isError ? describeError(error) : null;

  return (
    <div className="mx-auto max-w-4xl px-6 py-6">
      <h1 className="text-lg font-semibold mb-4">Automations</h1>

      {showSkeleton ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : errInfo ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
          <p className="font-medium text-destructive">{errInfo.title}</p>
          {errInfo.body ? (
            <p className="mt-1 text-xs text-muted-foreground">{errInfo.body}</p>
          ) : null}
        </div>
      ) : !data || data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No automations defined.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-card text-muted-foreground text-xs">
              <tr>
                <th className="text-left px-3 py-2">Name</th>
                <th className="text-left px-3 py-2">Schedule</th>
                <th className="text-left px-3 py-2">Next</th>
                <th className="text-left px-3 py-2">Agent</th>
                <th className="text-right px-3 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row: AutomationRow) => (
                <tr
                  key={row.name}
                  className={cn(
                    "border-t border-border hover:bg-accent/30",
                    !row.enabled && "opacity-50",
                  )}
                >
                  <td className="px-3 py-2 font-mono text-xs">
                    {row.name}
                    {row.skill_name ? (
                      <span className="ml-1 text-muted-foreground">
                        ({row.skill_name})
                      </span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-xs">{fmtSchedule(row)}</td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">
                    {row.enabled ? fmtNext(row.next_run_at) : "paused"}
                  </td>
                  <td className="px-3 py-2 text-xs">{row.agent ?? "—"}</td>
                  <td className="px-3 py-2 text-right space-x-2 whitespace-nowrap">
                    <button
                      type="button"
                      disabled={mutating}
                      onClick={() => fire.mutate(row.name)}
                      className="px-2 py-1 rounded text-xs bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
                    >
                      fire now
                    </button>
                    <button
                      type="button"
                      disabled={mutating}
                      onClick={() =>
                        patch.mutate({
                          name: row.name,
                          updates: { enabled: !row.enabled },
                        })
                      }
                      className={cn(
                        "px-2 py-1 rounded text-xs transition-colors",
                        row.enabled
                          ? "bg-destructive/15 text-destructive hover:bg-destructive/25"
                          : "bg-primary/10 text-primary hover:bg-primary/20",
                      )}
                    >
                      {row.enabled ? "pause" : "resume"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {fire.isError || patch.isError ? (
        <p className="mt-3 text-xs text-destructive">
          Action failed:{" "}
          {((fire.error || patch.error) as Error)?.message}. A token may be
          required — set localStorage["oma-dashboard-token"].
        </p>
      ) : null}
      {fire.isSuccess ? (
        <p className="mt-3 text-xs text-muted-foreground">
          Fired: {fire.data?.name} → {fire.data?.result}
        </p>
      ) : null}
    </div>
  );
}
