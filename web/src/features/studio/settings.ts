/**
 * Agent Settings form state ↔ `PATCH /api/agents/{id}` (SPEC §6.4 UpdateAgentRequest, §8.8).
 * Live fields apply immediately; config fields set `config_dirty` and wait for "Update version".
 * Only changed fields are sent.
 */
import type { AccentColor, Agent, Capability, RecordField, StarterQuestion } from "@/api/types";
import { normalizeRecordFields, validateRecordFields } from "@/features/records/schema";
import type { AvatarRef, FieldErrors } from "@/features/publish/wizard";
import { LIMITS } from "@/features/publish/wizard";
import type { UpdateAgentRequest } from "./api";

export interface ProfileForm {
  avatar: AvatarRef | null;
  title: string;
  tagline: string;
  description: string;
  greeting: string;
  input_placeholder: string;
  /** null = automatic questions (server defaults). */
  starter_questions: StarterQuestion[] | null;
  capabilities: Capability[];
  accent_color: AccentColor;
  listed: boolean;
  creator_name: string;
  creator_headline: string;
}

export interface RuntimeForm {
  soul: string;
  model: string;
  temperature: number | null;
  web_search_enabled: boolean;
  records_enabled: boolean;
  record_fields: RecordField[];
}

export function profileFromAgent(a: Agent): ProfileForm {
  return {
    avatar: a.avatar_id && a.avatar_url ? { avatar_id: a.avatar_id, avatar_url: a.avatar_url } : null,
    title: a.title,
    tagline: a.tagline,
    description: a.description,
    greeting: a.greeting,
    input_placeholder: a.input_placeholder,
    starter_questions: a.starter_questions ? a.starter_questions.map((q) => ({ ...q })) : null,
    capabilities: a.capabilities.map((c) => ({ ...c })),
    accent_color: a.accent_color,
    listed: a.listed,
    creator_name: a.creator_name,
    creator_headline: a.creator_headline,
  };
}

export function runtimeFromAgent(a: Agent): RuntimeForm {
  const fields = a.config.record_schema?.fields ?? [];
  return {
    soul: a.config.soul,
    model: a.config.model,
    temperature: a.config.temperature,
    web_search_enabled: a.config.web_search_enabled,
    records_enabled: fields.length > 0,
    record_fields: fields.map((f) => ({ ...f, options: f.options ? f.options.map((o) => ({ ...o })) : null })),
  };
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function cleanQuestions(qs: StarterQuestion[]): StarterQuestion[] {
  return qs.map((q) => ({ label: q.label.trim(), prompt: q.prompt.trim() })).filter((q) => q.label && q.prompt);
}

function cleanCapabilities(cs: Capability[]): Capability[] {
  return cs
    .map((c) => ({ icon: c.icon, name: c.name.trim(), description: c.description.trim() }))
    .filter((c) => c.name);
}

export function validateProfile(p: ProfileForm): FieldErrors {
  const e: FieldErrors = {};
  if (!p.title.trim()) e.title = "Give your agent a name.";
  else if (p.title.trim().length > LIMITS.title) e.title = `Keep the name under ${LIMITS.title} characters.`;
  (p.starter_questions ?? []).forEach((q, i) => {
    if (!q.label.trim() || !q.prompt.trim()) e[`starter_questions.${i}`] = "Fill in both the label and the question.";
  });
  p.capabilities.forEach((c, i) => {
    if (!c.name.trim()) e[`capabilities.${i}`] = "Name this capability or remove it.";
  });
  return e;
}

/** PATCH body with only the changed live fields (empty object = nothing to save). */
export function profilePatch(initial: ProfileForm, current: ProfileForm): UpdateAgentRequest {
  const body: UpdateAgentRequest = {};
  const text = ["title", "tagline", "description", "greeting", "input_placeholder", "creator_name", "creator_headline"] as const;
  for (const k of text) {
    const v = current[k].trim();
    if (v !== initial[k].trim()) body[k] = v;
  }
  if ((current.avatar?.avatar_id ?? null) !== (initial.avatar?.avatar_id ?? null))
    body.avatar_id = current.avatar?.avatar_id ?? null;
  if (current.accent_color !== initial.accent_color) body.accent_color = current.accent_color;
  if (current.listed !== initial.listed) body.listed = current.listed;
  const qs = current.starter_questions === null ? null : cleanQuestions(current.starter_questions);
  const qs0 = initial.starter_questions === null ? null : cleanQuestions(initial.starter_questions);
  if (!same(qs, qs0)) body.starter_questions = qs;
  const caps = cleanCapabilities(current.capabilities);
  if (!same(caps, cleanCapabilities(initial.capabilities))) body.capabilities = caps;
  return body;
}

export function validateRuntime(r: RuntimeForm): FieldErrors {
  const e: FieldErrors = {};
  if (!r.soul.trim()) e.soul = "The persona cannot be empty.";
  else if (r.soul.length > LIMITS.soul) e.soul = `Keep the persona under ${LIMITS.soul} characters.`;
  if (!r.model) e.model = "Pick a model.";
  if (r.records_enabled) {
    const problems = validateRecordFields(r.record_fields);
    if (problems.length > 0) e.records = problems[0];
  }
  return e;
}

function schemaOf(r: RuntimeForm) {
  const fields = r.records_enabled ? normalizeRecordFields(r.record_fields) : [];
  return fields.length > 0 ? { version: 1 as const, scope: "session" as const, fields } : null;
}

/** PATCH body `{config: {...changed}}`, or `{}` when nothing changed. */
export function runtimePatch(initial: RuntimeForm, current: RuntimeForm): UpdateAgentRequest {
  const config: NonNullable<UpdateAgentRequest["config"]> = {};
  if (current.soul.trim() !== initial.soul.trim()) config.soul = current.soul.trim();
  if (current.model !== initial.model) config.model = current.model;
  if (current.temperature !== initial.temperature) config.temperature = current.temperature;
  if (current.web_search_enabled !== initial.web_search_enabled) config.web_search_enabled = current.web_search_enabled;
  const schema = schemaOf(current);
  if (!same(schema, schemaOf(initial))) config.record_schema = schema;
  return Object.keys(config).length > 0 ? { config } : {};
}

/** Temperature presets (SPEC §4.4): model default always; 0.7 only for gemini-2.5* models. */
export function temperatureOptions(model: string, current: number | null): { value: string; label: string }[] {
  const opts = [{ value: "", label: "Model default (recommended)" }];
  if (model.startsWith("gemini-2.5") || current === 0.7) opts.push({ value: "0.7", label: "0.7 (more focused)" });
  if (current !== null && current !== 0.7) opts.push({ value: String(current), label: String(current) });
  return opts;
}
