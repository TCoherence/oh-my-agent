import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchSkillRecentTasks,
  fetchSkillsOverview,
  setSkillEnabled,
} from "@/lib/api";

/** Installed-catalog overview merged with runtime stats. Polls every 5s
 *  (matches sessions cadence — stats don't change faster than the bot
 *  finishes a task, which is many seconds at minimum). */
export function useSkillsOverview() {
  return useQuery({
    queryKey: ["skills-overview"],
    queryFn: fetchSkillsOverview,
    refetchInterval: 5000,
  });
}

/** Drill-down: recent runtime_tasks for one skill. Fetched lazily on row
 *  expand (enabled flag) so the index page doesn't N+1 the runtime DB. */
export function useSkillRecentTasks(name: string | null, opts?: { limit?: number }) {
  return useQuery({
    queryKey: ["skill-recent", name, opts?.limit ?? 10],
    queryFn: () => fetchSkillRecentTasks(name as string, { limit: opts?.limit ?? 10 }),
    enabled: name !== null,
    // Lower-rate refresh: drill-down doesn't need to be live like the index.
    refetchInterval: 15000,
  });
}

/** Toggle a skill's manual enable/disable. Invalidates the overview
 *  cache so the row flips without waiting for the next poll. */
export function useSetSkillEnabled() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      setSkillEnabled(name, enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["skills-overview"] });
    },
  });
}
