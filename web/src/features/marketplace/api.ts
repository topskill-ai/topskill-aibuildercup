import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Page, PublicAgentCard } from "@/api/types";

export const MARKETPLACE_PAGE_SIZE = 24;

/** Query key prefix for the marketplace list; `q` is the trimmed search string. */
export const publicAgentsKey = (q: string) => ["public-agents", q] as const;

export function fetchPublicAgents(q: string, cursor: string | null, signal?: AbortSignal) {
  return api.get<Page<PublicAgentCard>>("/api/public/agents", {
    query: { q: q || undefined, cursor: cursor ?? undefined, limit: MARKETPLACE_PAGE_SIZE },
    signal,
  });
}

/** `GET /api/public/agents?q=&cursor=&limit=24` as an infinite list (SPEC §6.4). */
export function usePublicAgents(q: string) {
  return useInfiniteQuery({
    queryKey: publicAgentsKey(q),
    queryFn: ({ pageParam, signal }) => fetchPublicAgents(q, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 60_000,
  });
}
