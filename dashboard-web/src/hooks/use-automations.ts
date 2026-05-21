import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchAutomations, fireAutomation, patchAutomation } from "@/lib/api";

/** Automation list with next-run times, polled every 5s. */
export function useAutomations() {
  return useQuery({
    queryKey: ["automations"],
    queryFn: fetchAutomations,
    refetchInterval: 5000,
  });
}

export function useFireAutomation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => fireAutomation(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["automations"] }),
  });
}

export function usePatchAutomation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      name,
      updates,
    }: {
      name: string;
      updates: { enabled?: boolean; cron?: string; interval_seconds?: number };
    }) => patchAutomation(name, updates),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["automations"] }),
  });
}
