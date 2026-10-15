/** Publish-flow API calls (SPEC §6.4 Skills, Agents — creator). */
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useDebouncedValue } from "@/features/studio/hooks";
import type { AccentColor, Agent, AgentDraft, Skill } from "@/api/types";
import { skillFormData, type SkillUpload } from "./files";
import { slugLocalCheck, type AvatarRef, type CreateAgentRequest, type SlugStatus } from "./wizard";

export type DraftQuality = "fast" | "best";

export interface SlugCheck {
  slug: string;
  valid: boolean;
  available: boolean;
  reason: "slug_invalid" | "slug_reserved" | "slug_taken" | null;
}

export function uploadSkill(skill: SkillUpload, signal?: AbortSignal): Promise<Skill> {
  return api.post<Skill>("/api/skills", skillFormData(skill), { signal });
}

export function draftAgent(skillIds: string[], quality: DraftQuality, signal?: AbortSignal): Promise<AgentDraft> {
  return api.post<AgentDraft>("/api/agents/draft", { skill_ids: skillIds, quality }, { signal });
}

export function checkSlug(slug: string, signal?: AbortSignal): Promise<SlugCheck> {
  return api.get<SlugCheck>("/api/agents/slug-check", { query: { slug }, signal });
}

export function uploadAvatar(file: File): Promise<AvatarRef> {
  const fd = new FormData();
  fd.append("file", file, file.name);
  return api.post<AvatarRef>("/api/avatars", fd);
}

export function generateAvatar(prompt: string, accent: AccentColor): Promise<AvatarRef> {
  return api.post<AvatarRef>("/api/avatars/generate", { prompt: prompt.slice(0, 600), accent_color: accent });
}

export function createAgent(body: CreateAgentRequest, signal?: AbortSignal): Promise<Agent> {
  return api.post<Agent>("/api/agents", body, { signal });
}

/**
 * Live slug availability (debounced `GET /api/agents/slug-check`). Blank = auto slug.
 * A failed check does not block publishing; the server re-checks on create.
 */
export function useSlugStatus(slug: string): SlugStatus {
  const trimmed = slug.trim();
  const debounced = useDebouncedValue(trimmed, 400);
  const local = slugLocalCheck(trimmed);
  const q = useQuery({
    queryKey: ["slug-check", debounced],
    queryFn: ({ signal }) => checkSlug(debounced, signal),
    enabled: local === null && debounced === trimmed,
    staleTime: 10_000,
    retry: false,
  });
  if (local) return local;
  if (debounced !== trimmed || q.isFetching || q.isPending) return { state: "checking" };
  if (q.isError) return { state: "error" };
  const data = q.data;
  if (!data) return { state: "checking" };
  return data.valid && data.available ? { state: "available" } : { state: "unavailable", reason: data.reason };
}
