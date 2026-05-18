import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useTrends } from "@/hooks/use-trends";
import type { TrendBucket } from "@/lib/api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/trends/")({
  component: TrendsPage,
});

const WINDOWS = [
  { weeks: 1, label: "1w" },
  { weeks: 2, label: "2w" },
  { weeks: 4, label: "4w" },
  { weeks: 12, label: "12w" },
] as const;

function fmtCost(n: number): string {
  return `$${n.toFixed(2)}`;
}

function fmtCompact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function TrendsPage() {
  const [weeks, setWeeks] = useState<number>(4);
  const { data, isLoading, isError, error } = useTrends(weeks);

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <div className="flex items-center justify-between gap-4 mb-4">
        <h1 className="text-lg font-semibold">Trends</h1>
        <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
          {WINDOWS.map((w) => (
            <button
              key={w.weeks}
              type="button"
              onClick={() => setWeeks(w.weeks)}
              className={cn(
                "px-3 py-1.5 transition-colors",
                weeks === w.weeks
                  ? "bg-primary text-primary-foreground"
                  : "bg-card text-muted-foreground hover:bg-accent/40",
              )}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </div>
      ) : null}

      {isError ? (
        <Card>
          <CardContent className="py-6 text-sm">
            <div className="text-red-500 font-medium">Failed to load trends</div>
            <div className="mt-2 text-xs text-muted-foreground">
              {(error as Error).message}
            </div>
          </CardContent>
        </Card>
      ) : null}

      {data ? (
        <div className="space-y-4">
          <SummaryRow data={data} />
          <ChartCard
            title="Spend"
            subtitle={`${fmtCost(data.totals.cost)} · ${fmtCompact(
              data.totals.in_tok + data.totals.out_tok,
            )} tokens`}
            buckets={data.buckets}
            value={(b) => b.cost}
            tooltip={(b) =>
              `${b.day}: ${fmtCost(b.cost)} · ${fmtCompact(
                b.in_tok + b.out_tok,
              )} tok`
            }
            barClass="bg-emerald-500"
          />
          <TaskChartCard buckets={data.buckets} totals={data.totals} />
          <ChartCard
            title="Conversation turns"
            subtitle={`${data.totals.turns} turns`}
            buckets={data.buckets}
            value={(b) => b.turns}
            tooltip={(b) => `${b.day}: ${b.turns} turns`}
            barClass="bg-sky-500"
          />
        </div>
      ) : null}
    </div>
  );
}

function SummaryRow({
  data,
}: {
  data: { days: number; totals: TrendsTotals; buckets: TrendBucket[] };
}) {
  const { totals } = data;
  const successRate =
    totals.task_total > 0
      ? Math.round((totals.task_success / totals.task_total) * 100)
      : null;
  const first = data.buckets[0]?.day ?? "—";
  const last = data.buckets[data.buckets.length - 1]?.day ?? "—";

  return (
    <Card>
      <CardContent className="py-4">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
          <Stat label="Spend" value={fmtCost(totals.cost)} />
          <Stat
            label="Tasks"
            value={String(totals.task_total)}
            sub={
              successRate === null
                ? "no runs"
                : `${successRate}% ok · ${totals.task_failed} failed`
            }
          />
          <Stat label="Turns" value={String(totals.turns)} />
          <Stat
            label="Window"
            value={`${data.days}d`}
            sub={`${first} → ${last}`}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function Stat({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-base font-semibold tabular-nums truncate">
        {value}
      </div>
      {sub ? (
        <div className="text-xs text-muted-foreground truncate">{sub}</div>
      ) : null}
    </div>
  );
}

interface TrendsTotals {
  cost: number;
  in_tok: number;
  out_tok: number;
  task_total: number;
  task_success: number;
  task_failed: number;
  turns: number;
}

function ChartCard({
  title,
  subtitle,
  buckets,
  value,
  tooltip,
  barClass,
}: {
  title: string;
  subtitle: string;
  buckets: TrendBucket[];
  value: (b: TrendBucket) => number;
  tooltip: (b: TrendBucket) => string;
  barClass: string;
}) {
  const max = Math.max(1, ...buckets.map(value));
  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex items-baseline justify-between mb-3">
          <div className="text-sm font-semibold">{title}</div>
          <div className="text-xs text-muted-foreground">{subtitle}</div>
        </div>
        <div className="flex items-end gap-px h-28">
          {buckets.map((b) => {
            const v = value(b);
            const pct = v > 0 ? Math.max(2, (v / max) * 100) : 0;
            return (
              <div
                key={b.day}
                title={tooltip(b)}
                className="flex-1 min-w-0 flex items-end h-full"
              >
                <div
                  className={cn("w-full rounded-sm", barClass)}
                  style={{ height: `${pct}%` }}
                />
              </div>
            );
          })}
        </div>
        <AxisLabels buckets={buckets} />
      </CardContent>
    </Card>
  );
}

function TaskChartCard({
  buckets,
  totals,
}: {
  buckets: TrendBucket[];
  totals: TrendsTotals;
}) {
  const max = Math.max(1, ...buckets.map((b) => b.task_total));
  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex items-baseline justify-between mb-3">
          <div className="text-sm font-semibold">Runtime tasks</div>
          <div className="text-xs text-muted-foreground">
            {totals.task_success} ok · {totals.task_failed} failed
          </div>
        </div>
        <div className="flex items-end gap-px h-28">
          {buckets.map((b) => {
            const okPct =
              b.task_success > 0
                ? Math.max(2, (b.task_success / max) * 100)
                : 0;
            const failPct =
              b.task_failed > 0 ? Math.max(2, (b.task_failed / max) * 100) : 0;
            return (
              <div
                key={b.day}
                title={`${b.day}: ${b.task_success} ok · ${b.task_failed} failed · ${b.task_total} total`}
                className="flex-1 min-w-0 flex flex-col justify-end h-full"
              >
                <div
                  className="w-full rounded-sm bg-rose-500"
                  style={{ height: `${failPct}%` }}
                />
                <div
                  className="w-full rounded-sm bg-emerald-500"
                  style={{ height: `${okPct}%` }}
                />
              </div>
            );
          })}
        </div>
        <AxisLabels buckets={buckets} />
      </CardContent>
    </Card>
  );
}

function AxisLabels({ buckets }: { buckets: TrendBucket[] }) {
  if (buckets.length === 0) return null;
  const first = buckets[0].day;
  const last = buckets[buckets.length - 1].day;
  return (
    <div className="flex justify-between mt-1.5 text-[10px] text-muted-foreground tabular-nums">
      <span>{first}</span>
      <span>{last}</span>
    </div>
  );
}
