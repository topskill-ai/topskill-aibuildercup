/** Records API (SPEC §6.4 Records). */
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api, downloadFile } from "@/api/client";
import { queryKeys } from "@/api/queryClient";
import type { ChatMessage, FollowStatus, RecordRow, RecordSchema } from "@/api/types";

export interface RecordFilters {
  status?: "in_progress" | "completed" | "";
  follow_status?: FollowStatus | "";
  /** Show PII values instead of "•••" (owner's own data). */
  reveal_pii?: boolean;
}

export interface RecordPage {
  schema: RecordSchema | null;
  items: RecordRow[];
  next_cursor: string | null;
}

export interface RecordDetail extends RecordRow {
  transcript: ChatMessage[];
  /** True when the record holds personal data and `reveal_pii` was off (the transcript is then empty). */
  transcript_hidden?: boolean;
}

const base = (agentId: string) => `/api/agents/${encodeURIComponent(agentId)}`;

export function fetchRecords(agentId: string, filters: RecordFilters, cursor: string | null): Promise<RecordPage> {
  return api.get<RecordPage>(`${base(agentId)}/records`, {
    query: {
      limit: 50,
      cursor,
      status: filters.status,
      follow_status: filters.follow_status,
      reveal_pii: filters.reveal_pii ? 1 : undefined,
    },
  });
}

export function useRecords(agentId: string, filters: RecordFilters) {
  return useInfiniteQuery({
    queryKey: queryKeys.records(agentId, { ...filters }),
    queryFn: ({ pageParam }) => fetchRecords(agentId, filters, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    placeholderData: keepPreviousData,
    enabled: Boolean(agentId),
  });
}

export function useRecordDetail(agentId: string, recordId: string, revealPii: boolean) {
  return useQuery({
    queryKey: [...queryKeys.records(agentId), "detail", recordId, revealPii],
    queryFn: () =>
      api.get<RecordDetail>(`${base(agentId)}/records/${encodeURIComponent(recordId)}`, {
        query: { reveal_pii: revealPii ? 1 : undefined },
      }),
    enabled: Boolean(agentId && recordId),
    placeholderData: keepPreviousData,
  });
}

export function patchRecord(
  agentId: string,
  recordId: string,
  body: { follow_status?: FollowStatus; follow_note?: string },
): Promise<RecordRow> {
  return api.patch<RecordRow>(`${base(agentId)}/records/${encodeURIComponent(recordId)}`, body);
}

export function batchFollow(agentId: string, ids: string[], follow_status: FollowStatus): Promise<{ updated: number }> {
  return api.post(`${base(agentId)}/records/follow`, { ids, follow_status });
}

/** `POST /records/follow` accepts at most this many ids per call (SPEC §6.4). */
export const FOLLOW_BATCH_MAX = 200;

/** Batch follow-up for any number of ids: posts chunks of 200 one after another and sums `updated`. */
export async function batchFollowAll(
  agentId: string,
  ids: string[],
  follow_status: FollowStatus,
  post: typeof batchFollow = batchFollow,
): Promise<{ updated: number }> {
  let updated = 0;
  for (let i = 0; i < ids.length; i += FOLLOW_BATCH_MAX) {
    const r = await post(agentId, ids.slice(i, i + FOLLOW_BATCH_MAX), follow_status);
    updated += r.updated;
  }
  return { updated };
}

export function exportRecordsCsv(agentId: string, slug: string): Promise<void> {
  return downloadFile(`${base(agentId)}/records/export.csv`, `${slug}-records.csv`);
}

export function validateRecordSchema(
  agentId: string,
  record_schema: { version: 1; scope: "session"; fields: RecordSchema["fields"] } | null,
): Promise<{ ok: boolean; record_schema: RecordSchema | null }> {
  return api.post(`${base(agentId)}/record-schema/validate`, { record_schema });
}

export const FOLLOW_STATUS_LABELS: Record<FollowStatus, string> = {
  new: "New",
  following: "Following up",
  done: "Done",
  invalid: "Invalid",
};
