import { useQuery } from "@tanstack/react-query";

import { fetchSessionSearch } from "@/lib/api";

/**
 * Full-text session search. Disabled until the query has ≥2 chars so a
 * single keystroke doesn't fire a MATCH. Not polled — results are a
 * point-in-time answer to an explicit query, not a live feed.
 */
export function useSessionSearch(query: string) {
  const q = query.trim();
  return useQuery({
    queryKey: ["session-search", q],
    queryFn: () => fetchSessionSearch({ q, limit: 50 }),
    enabled: q.length >= 2,
    staleTime: 10_000,
  });
}
