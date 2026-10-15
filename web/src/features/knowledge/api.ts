/** Knowledge API (SPEC §6.4 Knowledge) and the one-file-per-request upload queue. */
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { queryKeys } from "@/api/queryClient";
import type { KnowledgeFile, Passage } from "@/api/types";

export interface KnowledgeList {
  items: KnowledgeFile[];
  backend: string;
}

export function fetchKnowledge(agentId: string): Promise<KnowledgeList> {
  return api.get<KnowledgeList>(`/api/agents/${encodeURIComponent(agentId)}/knowledge`);
}

/** Polls every 3 s while any file is still processing (the server sweeps stale ones, §6.4). */
export function useKnowledge(agentId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.knowledge(agentId),
    queryFn: () => fetchKnowledge(agentId),
    enabled: enabled && Boolean(agentId),
    refetchInterval: (q) => (q.state.data?.items.some((f) => f.status === "processing") ? 3000 : false),
  });
}

/** Upload exactly one file (the web client never batches knowledge uploads, §6.1). */
export async function uploadKnowledgeFile(agentId: string, file: File, signal?: AbortSignal): Promise<KnowledgeFile> {
  const fd = new FormData();
  fd.append("files", file, file.name);
  const res = await api.post<{ items: KnowledgeFile[] }>(
    `/api/agents/${encodeURIComponent(agentId)}/knowledge`,
    fd,
    { signal },
  );
  const item = res.items[0];
  if (!item) throw new Error(`${file.name} was not accepted.`);
  return item;
}

export function deleteKnowledgeFile(agentId: string, fileId: string): Promise<{ ok: boolean }> {
  return api.delete(`/api/agents/${encodeURIComponent(agentId)}/knowledge/${encodeURIComponent(fileId)}`);
}

export function searchKnowledge(agentId: string, query: string): Promise<{ items: Passage[] }> {
  return api.post(`/api/agents/${encodeURIComponent(agentId)}/knowledge/search`, { query });
}
