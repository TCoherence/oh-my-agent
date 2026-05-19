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
          {data.warnings && data.warnings.length > 0 ? (
            <Card className="border-amber-500/40 bg-amber-500/10">
              <CardContent className="py-3 text-xs">
                <div className="font-medium text-amber-500">
                  Some signals unavailable — showing zeros for those
                </div>
                <ul className="mt-1 space-y-0.5 text-muted-foreground">
                  {data.warnings.map((w) => (
                    <li key={w} className="font-mono">
                      {w}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          ) : null}
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
            formatY={fmtCost}
          />
          <TaskChartCard buckets={data.buckets} totals={data.totals} />
          <ChartCard
            title="Conversation turns"
            subtitle={`${data.totals.turns} turns`}
            buckets={data.buckets}
            value={(b) => b.turns}
            tooltip={(b) => `${b.day}: ${b.turns} turns`}
            barClass="bg-sky-500"
            formatY={(n) => fmtCompact(Math.round(n))}
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

// Up to `n` evenly-spaced bucket dates, formatted MM-DD, for the x-axis.
function pickTicks(buckets: TrendBucket[], n: number): string[] {
  if (buckets.length === 0) return [];
  if (buckets.length <= n) return buckets.map((b) => b.day.slice(5));
  const out: string[] = [];
  for (let i = 0; i < n; i++) {
    const idx = Math.round((i * (buckets.length - 1)) / (n - 1));
    out.push(buckets[idx].day.slice(5));
  }
  return out;
}

interface BarSpec {
  key: string;
  tip: string;
  // bottom-up stack; rendered inside a flex-col justify-end column.
  segments: { pct: number; className: string }[];
}

function ChartFrame({
  title,
  subtitle,
  buckets,
  max,
  formatY,
  bars,
}: {
  title: string;
  subtitle: string;
  buckets: TrendBucket[];
  max: number;
  formatY: (n: number) => string;
  bars: BarSpec[];
}) {
  // Custom hover state → tooltip shows on the first mouseenter, with no
  // ~1s native `title=` delay (the Grafana-style instant readout).
  const [hover, setHover] = useState<number | null>(null);
  const n = bars.length;
  const frac = hover === null ? 0 : (hover + 0.5) / n;
  // Flip the tooltip near the edges so it doesn't clip off the plot.
  const tx = frac < 0.12 ? "0" : frac > 0.88 ? "-100%" : "-50%";

  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex items-baseline justify-between mb-3">
          <div className="text-sm font-semibold">{title}</div>
          <div className="text-xs text-muted-foreground">{subtitle}</div>
        </div>
        <div className="flex gap-2">
          {/* y-axis */}
          <div className="flex flex-col justify-between h-32 w-14 shrink-0 text-right text-[10px] text-muted-foreground tabular-nums">
            <span>{formatY(max)}</span>
            <span>{formatY(max / 2)}</span>
            <span>0</span>
          </div>
          {/* plot area with gridlines */}
          <div className="relative flex-1 h-32">
            <div className="absolute inset-x-0 top-0 border-t border-border/70" />
            <div className="absolute inset-x-0 top-1/2 border-t border-border/40" />
            <div className="absolute inset-x-0 bottom-0 border-t border-border/70" />
            {hover !== null ? (
              <div
                className="absolute -top-1.5 z-10 pointer-events-none rounded-md border border-border bg-accent px-2 py-1 text-[11px] text-foreground whitespace-nowrap shadow-lg"
                style={{
                  left: `${frac * 100}%`,
                  transform: `translate(${tx}, -100%)`,
                }}
              >
                {bars[hover].tip}
              </div>
            ) : null}
            <div className="absolute inset-0 flex items-end gap-px">
              {bars.map((bar, i) => (
                <div
                  key={bar.key}
                  onMouseEnter={() => setHover(i)}
                  onMouseLeave={() =>
                    setHover((h) => (h === i ? null : h))
                  }
                  className={cn(
                    "flex-1 min-w-0 flex flex-col justify-end h-full",
                    hover === i && "bg-foreground/[0.06]",
                  )}
                >
                  {bar.segments.map((s, si) => (
                    <div
                      key={si}
                      className={cn("w-full rounded-sm", s.className)}
                      style={{ height: `${s.pct}%` }}
                    />
                  ))}
                </div>
              ))}
            </div>
          </div>
        </div>
        {/* x-axis */}
        <div className="flex gap-2 mt-1.5">
          <div className="w-14 shrink-0" />
          <div className="flex-1 flex justify-between text-[10px] text-muted-foreground tabular-nums">
            {pickTicks(buckets, 6).map((t, i) => (
              <span key={`${t}-${i}`}>{t}</span>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function ChartCard({
  title,
  subtitle,
  buckets,
  value,
  tooltip,
  barClass,
  formatY,
}: {
  title: string;
  subtitle: string;
  buckets: TrendBucket[];
  value: (b: TrendBucket) => number;
  tooltip: (b: TrendBucket) => string;
  barClass: string;
  formatY: (n: number) => string;
}) {
  const max = Math.max(1, ...buckets.map(value));
  const bars: BarSpec[] = buckets.map((b) => {
    const v = value(b);
    return {
      key: b.day,
      tip: tooltip(b),
      segments: [
        { pct: v > 0 ? Math.max(2, (v / max) * 100) : 0, className: barClass },
      ],
    };
  });
  return (
    <ChartFrame
      title={title}
      subtitle={subtitle}
      buckets={buckets}
      max={max}
      formatY={formatY}
      bars={bars}
    />
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
  const bars: BarSpec[] = buckets.map((b) => ({
    key: b.day,
    tip: `${b.day}: ${b.task_success} ok · ${b.task_failed} failed · ${b.task_total} total`,
    segments: [
      {
        pct: b.task_failed > 0 ? Math.max(2, (b.task_failed / max) * 100) : 0,
        className: "bg-rose-500",
      },
      {
        pct: b.task_success > 0 ? Math.max(2, (b.task_success / max) * 100) : 0,
        className: "bg-emerald-500",
      },
    ],
  }));
  return (
    <ChartFrame
      title="Runtime tasks"
      subtitle={`${totals.task_success} ok · ${totals.task_failed} failed`}
      buckets={buckets}
      max={max}
      formatY={(n) => String(Math.round(n))}
      bars={bars}
    />
  );
}
