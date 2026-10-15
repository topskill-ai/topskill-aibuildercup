import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { queryKeys } from "@/api/queryClient";
import type { BillingStatus, ConversationOut, LedgerEntry, PublicAgent, Wallet } from "@/api/types";

const agentPath = (slug: string) => `/api/public/agents/${encodeURIComponent(slug)}`;

export const chatPath = (slug: string) => `${agentPath(slug)}/chat`;

/** `GET /api/public/agents/{slug}` → `PublicAgent` (404 `agent_not_found`, 410 `agent_offline`). */
export function usePublicAgent(slug: string) {
  return useQuery({
    queryKey: queryKeys.publicAgent(slug),
    queryFn: ({ signal }) => api.get<PublicAgent>(agentPath(slug), { signal }),
    enabled: Boolean(slug),
    staleTime: 60_000,
  });
}

/** `GET /api/public/agents/{slug}/billing` (signed-in only). */
export function useBillingStatus(slug: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.billing(slug),
    queryFn: ({ signal }) => api.get<BillingStatus>(`${agentPath(slug)}/billing`, { signal }),
    enabled: enabled && Boolean(slug),
    staleTime: 10_000,
  });
}

export function fetchConversation(slug: string, conversationId: string, signal?: AbortSignal) {
  return api.get<ConversationOut>(`${agentPath(slug)}/conversations/${encodeURIComponent(conversationId)}`, {
    signal,
  });
}

export function abortTurn(slug: string, conversationId: string, turnId: string) {
  return api.post<{ aborted: boolean }>(`${chatPath(slug)}/abort`, {
    conversation_id: conversationId,
    turn_id: turnId,
  });
}

/** `POST /api/wallet/demo-topup`; refreshes the wallet and every billing status on success. */
export function useDemoTopup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ wallet: Wallet; entry: LedgerEntry }>("/api/wallet/demo-topup", {}),
    onSuccess: (res) => {
      qc.setQueryData(queryKeys.wallet, res.wallet);
      void qc.invalidateQueries({ queryKey: ["billing"] });
      void qc.invalidateQueries({ queryKey: ["ledger"] });
    },
  });
}
