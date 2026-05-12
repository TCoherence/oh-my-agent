import { useQuery } from "@tanstack/react-query";

import { fetchSessionHistory } from "@/lib/api";

/**
 * Chat history for one thread. Polled every 2s — the user is actively
 * watching this page; new turns from the bot should appear within
 * one cache cycle. before_id is intentionally NOT in the query key
 * because backward scrolling is loaded once and prepended in-place
 * by the UI; we don't want pagination to invalidate the live tail.
 */
export function useSessionHistory(opts: {
  platform: string;
  channelId: string;
  threadId: string;
  limit?: number;
}) {
  return useQuery({
    queryKey: [
      "session-history",
      opts.platform,
      opts.channelId,
      opts.threadId,
      opts.limit ?? 200,
    ],
    queryFn: () =>
      fetchSessionHistory({
        platform: opts.platform,
        channelId: opts.channelId,
        threadId: opts.threadId,
        limit: opts.limit,
      }),
    refetchInterval: 2000,
    // Important: enabled false when any path param is empty — prevents
    // the brief "" → "real-id" router transition from firing a 422.
    enabled: Boolean(opts.platform && opts.channelId && opts.threadId),
  });
}
