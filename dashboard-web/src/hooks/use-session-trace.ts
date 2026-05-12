import { useQuery } from "@tanstack/react-query";

import { fetchSessionTrace } from "@/lib/api";

/**
 * Tool-call trace events for one thread on one day. Polled every 2s
 * (sibling to history). The ``date`` param is required by the backend
 * — see trace_reader.py for why we don't auto-scan all days.
 */
export function useSessionTrace(opts: {
  platform: string;
  channelId: string;
  threadId: string;
  date: string;
}) {
  return useQuery({
    queryKey: [
      "session-trace",
      opts.platform,
      opts.channelId,
      opts.threadId,
      opts.date,
    ],
    queryFn: () =>
      fetchSessionTrace({
        platform: opts.platform,
        channelId: opts.channelId,
        threadId: opts.threadId,
        date: opts.date,
      }),
    refetchInterval: 2000,
    enabled: Boolean(
      opts.platform && opts.channelId && opts.threadId && opts.date,
    ),
  });
}
