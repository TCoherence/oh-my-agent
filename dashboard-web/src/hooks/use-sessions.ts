import { useQuery } from "@tanstack/react-query";

import { fetchSessionList } from "@/lib/api";

/**
 * Paginated session list. Polled every 5s — slow enough to not hammer
 * the GROUP BY query on large turns tables, fast enough to surface
 * new threads as the bot creates them.
 */
export function useSessions(opts: { limit?: number; cursor?: string | null } = {}) {
  return useQuery({
    queryKey: ["sessions", opts.limit ?? 50, opts.cursor ?? null],
    queryFn: () => fetchSessionList({ limit: opts.limit, cursor: opts.cursor ?? null }),
    refetchInterval: 5000,
  });
}
