/**
 * Payment mandate hooks (SPEC §6.4 "Mandates", §8.6). Creating a mandate is MUST; list, revoke and
 * calls are [SHOULD] endpoints — when the backend lacks them (404) the UI hides those parts.
 */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { hasCode, isApiError } from "@/api/errors";
import { queryKeys } from "@/api/queryClient";
import type { A2ACall, Mandate, Page } from "@/api/types";

export interface CreateMandateRequest {
  audience: string;
  intent: string;
  max_amount_cents: number;
  budget_cents: number;
  ttl_minutes: number;
  payer_agent?: string;
}

export interface CreateMandateResponse {
  mandate: Mandate;
  token: string;
}

export function createMandate(body: CreateMandateRequest): Promise<CreateMandateResponse> {
  return api.post<CreateMandateResponse>("/api/mandates", body);
}

export function useCreateMandate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: createMandate,
    onSuccess: () => void qc.invalidateQueries({ queryKey: queryKeys.mandates }),
  });
}

export const mandateKeys = {
  pages: [...queryKeys.mandates, { view: "pages" }] as const,
  calls: (mandateId: string) => [...queryKeys.mandates, mandateId, "calls"] as const,
};

/** True for the 404 FastAPI returns when a [SHOULD] endpoint is not deployed. */
export function isMissingEndpoint(err: unknown): boolean {
  return isApiError(err) && err.status === 404 && hasCode(err, "not_found");
}

export function useMandates(opts: { refetchInterval?: number | false } = {}) {
  return useInfiniteQuery({
    queryKey: mandateKeys.pages,
    queryFn: ({ pageParam }) =>
      api.get<Page<Mandate>>("/api/mandates", { query: { cursor: pageParam, limit: 20 } }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    refetchInterval: opts.refetchInterval,
  });
}

export function useMandateCalls(mandateId: string | null, opts: { enabled?: boolean; refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: mandateKeys.calls(mandateId ?? "none"),
    queryFn: () => api.get<{ items: A2ACall[] }>(`/api/mandates/${encodeURIComponent(mandateId ?? "")}/calls`),
    enabled: Boolean(mandateId) && (opts.enabled ?? true),
    refetchInterval: opts.refetchInterval,
  });
}

export function useRevokeMandate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mandateId: string) => api.post<Mandate>(`/api/mandates/${encodeURIComponent(mandateId)}/revoke`, {}),
    onSuccess: () => void qc.invalidateQueries({ queryKey: queryKeys.mandates }),
  });
}
