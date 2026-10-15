/**
 * Wallet data hooks (SPEC §6.4 "Wallet & billing"): balance, paginated ledger, demo top-up.
 */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { queryKeys } from "@/api/queryClient";
import type { LedgerCategory, LedgerEntry, Page, Wallet } from "@/api/types";

export const LEDGER_PAGE_SIZE = 25;

export function fetchWallet(): Promise<Wallet> {
  return api.get<Wallet>("/api/wallet");
}

export function useWallet() {
  return useQuery({ queryKey: queryKeys.wallet, queryFn: fetchWallet, staleTime: 15_000 });
}

export interface LedgerFilters {
  category?: LedgerCategory | null;
  agentId?: string | null;
}

export function fetchLedgerPage(filters: LedgerFilters, cursor: string | null, limit = LEDGER_PAGE_SIZE) {
  return api.get<Page<LedgerEntry>>("/api/wallet/ledger", {
    query: { category: filters.category ?? null, agent_id: filters.agentId ?? null, cursor, limit },
  });
}

/** Full ledger with "Load more" (newest first). Cached under `["ledger", {..., view: "pages"}]`. */
export function useLedger(filters: LedgerFilters) {
  return useInfiniteQuery({
    queryKey: queryKeys.ledger({ category: filters.category ?? null, agent_id: filters.agentId ?? null, view: "pages" }),
    queryFn: ({ pageParam }) => fetchLedgerPage(filters, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
}

/** The latest `limit` entries of one category (e.g. the assistant's mini ledger of A2A payments). */
export function useRecentLedger(category: LedgerCategory, limit = 5, opts: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: queryKeys.ledger({ category, view: "recent", limit }),
    queryFn: () => fetchLedgerPage({ category }, null, limit),
    refetchInterval: opts.refetchInterval,
  });
}

export interface TopupResponse {
  wallet: Wallet;
  entry: LedgerEntry;
}

/** `POST /api/wallet/demo-topup`; refreshes the balance and every ledger view on success. */
export function useDemoTopup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<TopupResponse>("/api/wallet/demo-topup", {}),
    onSuccess: (res) => {
      qc.setQueryData(queryKeys.wallet, res.wallet);
      void qc.invalidateQueries({ queryKey: ["ledger"] });
      void qc.invalidateQueries({ queryKey: queryKeys.wallet });
    },
  });
}
