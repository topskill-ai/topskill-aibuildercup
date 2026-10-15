/** "Ask your data" hooks (SPEC §18 analytics). */
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { AnalyticsAnswer, AnalyticsStatus } from "./types";

export const analyticsKeys = { status: ["analytics", "status"] as const };

/** Feature status; a backend without the analytics routes (404) simply hides the card. */
export function useAnalyticsStatus() {
  return useQuery({
    queryKey: analyticsKeys.status,
    queryFn: () => api.get<AnalyticsStatus>("/api/analytics/status"),
    retry: false,
    staleTime: 60_000,
  });
}

export function useAskData() {
  return useMutation({
    mutationFn: (question: string) => api.post<AnalyticsAnswer>("/api/analytics/ask", { question }),
  });
}
