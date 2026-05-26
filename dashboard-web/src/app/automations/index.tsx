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
        title: "Automations unavailable",
        body:
          "The dashboard returned 503. This usually means the YAML directory itself is unreachable — check `automations.storage_dir` in config.yaml.",
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
  const showSkeleton = data === undefined && !isError;
  const errInfo = isError ? describeError(error) : null;
  // Treat the absence of `mode` as "live" — older backends only ever
  // answered when the colocated scheduler was up, so any pre-fallback
  // success was a live answer.
  const mode = data?.mode ?? "live";
  const isStatic = mode === "static";

  return (
    <div className="mx-auto max-w-4xl px-6 py-6">
      <h1 className="text-lg font-semibold mb-2">Automations</h1>

      {/* Static-mode banner — placed BEFORE the table so the operator
          sees the read-only caveat before reasoning about disabled
          buttons. Don't render under skeleton / error states. */}
      {!showSkeleton && !errInfo && isStatic ? (
        <div className="mb-3 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          <span className="font-medium">Read-only view.</span> This
          standalone dashboard reads schedules straight from{" "}
          <code>automations.storage_dir</code>. <span className="font-medium">Next-run times and
          fire/pause controls require the colocated dashboard</span>{" "}
          (set <code>dashboard.colocated: true</code>, default port :8765).
        </div>
      ) : null}

      {!showSkeleton && !errInfo && data?.warnings && data.warnings.length > 0 ? (
        <div className="mb-3 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          {data.warnings.map((w, i) => (
            <div key={i}>⚠ {w}</div>
          ))}
        </div>
      ) : null}

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
                    {isStatic
                      ? "—"
                      : row.enabled
                      ? fmtNext(row.next_run_at)
                      : "paused"}
                  </td>
                  <td className="px-3 py-2 text-xs">{row.agent ?? "—"}</td>
                  <td className="px-3 py-2 text-right space-x-2 whitespace-nowrap">
                    <button
                      type="button"
                      disabled={mutating || isStatic}
                      onClick={() => fire.mutate(row.name)}
                      title={
                        isStatic
                          ? "Requires colocated dashboard"
                          : "Run this automation immediately"
                      }
                      className={cn(
                        "px-2 py-1 rounded text-xs transition-colors",
                        isStatic
                          ? "bg-muted text-muted-foreground cursor-not-allowed"
                          : "bg-primary/10 text-primary hover:bg-primary/20",
                      )}
                    >
                      fire now
                    </button>
                    <button
                      type="button"
                      disabled={mutating || isStatic}
                      onClick={() =>
                        patch.mutate({
                          name: row.name,
                          updates: { enabled: !row.enabled },
                        })
                      }
                      title={
                        isStatic
                          ? "Requires colocated dashboard"
                          : row.enabled
                          ? "Pause scheduling"
                          : "Resume scheduling"
                      }
                      className={cn(
                        "px-2 py-1 rounded text-xs transition-colors",
                        isStatic
                          ? "bg-muted text-muted-foreground cursor-not-allowed"
                          : row.enabled
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
