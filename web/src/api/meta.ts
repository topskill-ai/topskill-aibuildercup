import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import { queryKeys } from "./queryClient";
import type { Meta } from "./types";

export function fetchMeta(): Promise<Meta> {
  return api.get<Meta>("/api/public/meta");
}

/**
 * Public platform constants (`GET /api/public/meta`): chat models, revenue share, grants,
 * mandate defaults. Fetched once per session (`staleTime: Infinity`); never hardcode these values.
 */
export function useMeta() {
  return useQuery({ queryKey: queryKeys.meta, queryFn: fetchMeta, staleTime: Infinity, gcTime: Infinity });
}
