/** Publish-flow API calls (SPEC §6.4 Skills, Agents — creator). */
import { api } from "@/api/client";
import type { AccentColor, Agent, AgentDraft, Skill } from "@/api/types";
import { skillFormData, type SkillUpload } from "./files";
import type { AvatarRef, CreateAgentRequest } from "./wizard";

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

export function createAgent(body: CreateAgentRequest): Promise<Agent> {
  return api.post<Agent>("/api/agents", body);
}
