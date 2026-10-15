/** Creator agent + dashboard API hooks (SPEC §6.4 Agents — creator, Wallet & billing). */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { queryKeys } from "@/api/queryClient";
import type {
  Agent,
  AgentConfig,
  AgentStats,
  AgentSummary,
  Capability,
  ConversationSummary,
  Dashboard,
  LedgerEntry,
  Page,
  Pricing,
  StarterQuestion,
  VersionSummary,
  AccentColor,
  RecordField,
} from "@/api/types";

const base = (agentId: string) => `/api/agents/${encodeURIComponent(agentId)}`;

export interface UpdateAgentRequest {
  title?: string;
  tagline?: string;
  description?: string;
  avatar_id?: string | null;
  greeting?: string;
  input_placeholder?: string;
  starter_questions?: StarterQuestion[] | null;
  capabilities?: Capability[];
  accent_color?: AccentColor;
  listed?: boolean;
  creator_name?: string;
  creator_headline?: string;
  pricing?: Pricing;
  config?: Partial<Omit<AgentConfig, "record_schema">> & {
    record_schema?: { version: 1; scope: "session"; fields: RecordField[] } | null;
  };
}

export function useDashboard(opts: { live?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.get<Dashboard>("/api/dashboard"),
    refetchOnWindowFocus: true,
    refetchInterval: opts.live ? 5000 : false,
    refetchIntervalInBackground: false,
  });
}

export function useAgents() {
  return useQuery({
    queryKey: queryKeys.agents,
    queryFn: () => api.get<{ items: AgentSummary[] }>("/api/agents"),
  });
}

export function useAgent(agentId: string, opts: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.agent(agentId),
    queryFn: () => api.get<Agent>(base(agentId)),
    enabled: Boolean(agentId),
    refetchInterval: (q) => (opts.poll || (q.state.data?.knowledge.processing ?? 0) > 0 ? 4000 : false),
  });
}

export interface Earnings {
  stats: AgentStats;
  items: LedgerEntry[];
  next_cursor: string | null;
}

export function useEarnings(agentId: string, limit = 10) {
  return useQuery({
    queryKey: ["earnings", agentId, limit],
    queryFn: () => api.get<Earnings>(`${base(agentId)}/earnings`, { query: { limit } }),
    enabled: Boolean(agentId),
  });
}

export function useVersions(agentId: string) {
  return useQuery({
    queryKey: ["versions", agentId],
    queryFn: () => api.get<{ items: VersionSummary[] }>(`${base(agentId)}/versions`),
    enabled: Boolean(agentId),
    retry: false,
  });
}

export function useConversations(agentId: string, limit = 5) {
  return useQuery({
    queryKey: ["conversations", agentId, limit],
    queryFn: () => api.get<Page<ConversationSummary>>(`${base(agentId)}/conversations`, { query: { limit } }),
    enabled: Boolean(agentId),
    retry: false,
  });
}

export const agentApi = {
  update: (agentId: string, body: UpdateAgentRequest) => api.patch<Agent>(base(agentId), body),
  newVersion: (agentId: string, note: string) =>
    api.post<Agent>(`${base(agentId)}/versions`, note.trim() ? { note: note.trim() } : {}),
  activate: (agentId: string, n: number) => api.post<Agent>(`${base(agentId)}/versions/${n}/activate`),
  pause: (agentId: string) => api.post<Agent>(`${base(agentId)}/pause`),
  resume: (agentId: string) => api.post<Agent>(`${base(agentId)}/resume`),
  remove: (agentId: string) => api.delete<{ ok: boolean }>(base(agentId)),
};

/** Put a fresh `Agent` into the cache and invalidate lists that summarise it. */
export function useAgentCacheUpdater() {
  const qc = useQueryClient();
  return (agent: Agent) => {
    qc.setQueryData(queryKeys.agent(agent.id), agent);
    void qc.invalidateQueries({ queryKey: queryKeys.agents });
    void qc.invalidateQueries({ queryKey: queryKeys.dashboard });
    void qc.invalidateQueries({ queryKey: ["versions", agent.id] });
  };
}
