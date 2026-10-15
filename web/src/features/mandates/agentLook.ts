import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { queryKeys } from "@/api/queryClient";
import type { AccentColor, PublicAgent } from "@/api/types";
import { accentForSlug } from "./accent";

export interface AgentLook {
  avatarUrl: string | null;
  accent: AccentColor;
  title: string | null;
  creatorName: string | null;
}

/**
 * Avatar and accent of a marketplace agent, for timeline cards (directory frames carry neither).
 * Shares the `["public-agent", slug]` cache with the agent page; failures fall back to a monogram.
 */
export function useAgentLook(slug: string): AgentLook {
  const q = useQuery({
    queryKey: queryKeys.publicAgent(slug),
    queryFn: () => api.get<PublicAgent>(`/api/public/agents/${encodeURIComponent(slug)}`),
    enabled: Boolean(slug),
    staleTime: 5 * 60_000,
    retry: false,
  });
  return {
    avatarUrl: q.data?.avatar_url ?? null,
    accent: q.data?.accent_color ?? accentForSlug(slug),
    title: q.data?.title ?? null,
    creatorName: q.data?.creator?.name ?? null,
  };
}
