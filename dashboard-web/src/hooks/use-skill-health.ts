import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchSkillHealth, setSkillEnabled } from "@/lib/api";

/** Per-skill health, polled every 5s (matches sessions cadence). */
export function useSkillHealth() {
  return useQuery({
    queryKey: ["skill-health"],
    queryFn: fetchSkillHealth,
    refetchInterval: 5000,
  });
}

/** Toggle a skill's manual enable/disable; refetches health on success. */
export function useSetSkillEnabled() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      setSkillEnabled(name, enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["skill-health"] });
    },
  });
}
