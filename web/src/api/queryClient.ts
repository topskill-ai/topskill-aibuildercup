import { QueryClient } from "@tanstack/react-query";
import { isApiError } from "./errors";

/** Retry transient failures only: never 4xx (the answer will not change), at most twice. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  if (isApiError(error)) {
    if (error.status >= 400 && error.status < 500) return false;
    return error.status === 0 || error.status >= 500;
  }
  return true;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: shouldRetry,
        refetchOnWindowFocus: true,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

/** App-wide singleton (also cleared on sign-out, SPEC §11.5). */
export const queryClient = createQueryClient();

/** Shared query keys (SPEC §11.6). Feature folders build their keys from these prefixes. */
export const queryKeys = {
  meta: ["meta"] as const,
  me: ["me"] as const,
  wallet: ["wallet"] as const,
  agents: ["agents"] as const,
  agent: (id: string) => ["agent", id] as const,
  publicAgent: (slug: string) => ["public-agent", slug] as const,
  ledger: (filters: Record<string, unknown> = {}) => ["ledger", filters] as const,
  records: (agentId: string, filters: Record<string, unknown> = {}) => ["records", agentId, filters] as const,
  knowledge: (agentId: string) => ["knowledge", agentId] as const,
  mandates: ["mandates"] as const,
  dashboard: ["dashboard"] as const,
  billing: (slug: string) => ["billing", slug] as const,
};
