import { useQuery } from "@tanstack/react-query";

import { fetchTrends } from "@/lib/api";

/**
 * Daily trend series over a trailing N-week window. Polled every 30s —
 * trends move on the order of automation runs (hours), so a slow tick
 * keeps the page live without hammering the daily-bucket aggregation.
 */
export function useTrends(weeks: number) {
  return useQuery({
    queryKey: ["trends", weeks],
    queryFn: () => fetchTrends({ weeks }),
    refetchInterval: 30000,
  });
}
