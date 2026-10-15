/**
 * Publish wizard state (SPEC §11.4 "Publish wizard"). Pure functions only, so every rule
 * (draft prefill, step validation, pricing line, request building) is unit-tested without a DOM.
 */
import type {
  AccentColor,
  AgentDraft,
  Capability,
  Pricing,
  PricingMode,
  RecordField,
  Skill,
  StarterQuestion,
} from "@/api/types";
import { creatorShareCents, formatCredits, formatShareBp, parseCreditsToCents } from "@/lib/money";
import { normalizeRecordFields, validateRecordFields } from "@/features/records/schema";

export const WIZARD_STEPS = ["upload", "identity", "pricing", "golive"] as const;
export type WizardStep = (typeof WIZARD_STEPS)[number];

export const STEP_LABELS: Record<WizardStep, string> = {
  upload: "Upload",
  identity: "Identity",
  pricing: "Pricing",
  golive: "Go live",
};

/** Limits mirrored from §5.2 / §6.4 so the form can explain problems before the server does. */
export const LIMITS = {
  title: 40,
  tagline: 80,
  description: 1000,
  soul: 6000,
  greeting: 600,
  placeholder: 120,
  starterLabel: 60,
  starterPrompt: 300,
  starterQuestions: 3,
  capabilities: 6,
  capabilityName: 30,
  capabilityDescription: 120,
  maxPriceCents: 100_000,
  maxFreeRounds: 100,
} as const;

export const SLUG_RE = /^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$/;

export interface AvatarRef {
  avatar_id: string;
  avatar_url: string;
}

export interface IdentityForm {
  title: string;
  tagline: string;
  description: string;
  soul: string;
  greeting: string;
  input_placeholder: string;
  starter_questions: StarterQuestion[];
  capabilities: Capability[];
  accent_color: AccentColor;
  avatar: AvatarRef | null;
  avatar_prompt: string;
}

export interface PricingForm {
  mode: PricingMode;
  /** Credits as typed ("1", "1.50"). */
  price: string;
  free_rounds: string;
  a2a_enabled: boolean;
  a2a_price: string;
  /** Once the creator edits the A2A price it stops following the round price. */
  a2a_price_touched: boolean;
  rationale: string;
}

export interface GoLiveForm {
  /** Blank = auto slug (§8.7). */
  slug: string;
  model: string;
  web_search_enabled: boolean;
  listed: boolean;
}

export interface RecordsForm {
  enabled: boolean;
  fields: RecordField[];
}

export interface WizardForm {
  identity: IdentityForm;
  pricing: PricingForm;
  golive: GoLiveForm;
  records: RecordsForm;
}

export function emptyIdentity(): IdentityForm {
  return {
    title: "",
    tagline: "",
    description: "",
    soul: "",
    greeting: "",
    input_placeholder: "",
    starter_questions: [],
    capabilities: [],
    accent_color: "blue",
    avatar: null,
    avatar_prompt: "",
  };
}

export function emptyForm(defaultModel = ""): WizardForm {
  return {
    identity: emptyIdentity(),
    pricing: {
      mode: "per_round",
      price: "1.00",
      free_rounds: "2",
      a2a_enabled: true,
      a2a_price: "1.00",
      a2a_price_touched: false,
      rationale: "",
    },
    golive: { slug: "", model: defaultModel, web_search_enabled: true, listed: true },
    records: { enabled: false, fields: [] },
  };
}

function centsToInput(cents: number): string {
  return formatCredits(Math.max(0, cents)).replace(/,/g, "");
}

function clip(text: string, max: number): string {
  const t = (text ?? "").trim();
  return t.length <= max ? t : `${t.slice(0, max - 1)}…`;
}

/** Prefill the whole form from a Gemini draft; keeps the uploaded avatar and the model choice. */
export function formFromDraft(draft: AgentDraft, previous: WizardForm): WizardForm {
  const priceCents = draft.pricing.mode === "free" ? 0 : Math.max(0, Math.trunc(draft.pricing.price_cents));
  const price = draft.pricing.mode === "free" ? previous.pricing.price : centsToInput(priceCents);
  const fields = normalizeRecordFields(draft.record_fields ?? []);
  return {
    identity: {
      title: clip(draft.title, LIMITS.title),
      tagline: clip(draft.tagline, LIMITS.tagline),
      description: clip(draft.description, LIMITS.description),
      soul: clip(draft.soul, LIMITS.soul),
      greeting: clip(draft.greeting, LIMITS.greeting),
      input_placeholder: clip(draft.input_placeholder, LIMITS.placeholder),
      starter_questions: (draft.starter_questions ?? []).slice(0, LIMITS.starterQuestions).map((q) => ({
        label: clip(q.label, LIMITS.starterLabel),
        prompt: clip(q.prompt, LIMITS.starterPrompt),
      })),
      capabilities: (draft.capabilities ?? []).slice(0, LIMITS.capabilities).map((c) => ({
        icon: c.icon || "sparkles",
        name: clip(c.name, LIMITS.capabilityName),
        description: clip(c.description, LIMITS.capabilityDescription),
      })),
      accent_color: draft.accent_color ?? previous.identity.accent_color,
      avatar: previous.identity.avatar,
      avatar_prompt: draft.avatar_prompt ?? "",
    },
    pricing: {
      mode: draft.pricing.mode,
      price,
      free_rounds: String(Math.min(LIMITS.maxFreeRounds, Math.max(0, Math.trunc(draft.pricing.free_rounds)))),
      a2a_enabled: previous.pricing.a2a_enabled,
      a2a_price: price,
      a2a_price_touched: false,
      rationale: draft.pricing.rationale ?? "",
    },
    golive: { ...previous.golive, web_search_enabled: Boolean(draft.web_search_recommended) },
    records: { enabled: fields.length > 0, fields },
  };
}

/**
 * Template prefill used when drafting fails (502 `draft_failed`, SPEC §8.7): the creator can
 * still publish by editing a reasonable starting point built from the skill metadata.
 */
export function templateDraft(skills: Pick<Skill, "name" | "description">[]): AgentDraft {
  const name = skills[0]?.name?.trim() || "My agent";
  const description = skills[0]?.description?.trim() || `An AI agent built from the ${name} skill.`;
  const title = clip(name.replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()), LIMITS.title);
  return {
    title,
    tagline: clip(description, LIMITS.tagline),
    description,
    soul:
      `You are ${title}, an expert assistant. ${description} ` +
      "First understand the visitor's situation by asking one focused question at a time, then give " +
      "clear, practical guidance based only on your skill. When information is missing, say what you " +
      "need. Stay within your area of expertise and recommend a qualified professional for anything else.",
    greeting: `Hi, I'm ${title}. What would you like help with today?`,
    input_placeholder: "Describe your situation…",
    starter_questions: [
      { label: "What can you help me with?", prompt: "What can you help me with?" },
      { label: "Where should I start?", prompt: "I'm new to this. Where should I start?" },
      { label: "Review my situation", prompt: "Can you review my situation and suggest next steps?" },
    ],
    capabilities: [{ icon: "sparkles", name: clip(title, LIMITS.capabilityName), description: clip(description, LIMITS.capabilityDescription) }],
    accent_color: "blue",
    pricing: { mode: "per_round", price_cents: 100, free_rounds: 2, rationale: "" },
    record_fields: [],
    web_search_recommended: true,
    avatar_prompt: `A friendly expert representing ${title}`,
    model_used: "template",
  };
}

/** True when the identity differs from what the last draft produced (Redraft asks to confirm). */
export function identityEdited(current: IdentityForm, drafted: IdentityForm | null): boolean {
  if (!drafted) return false;
  const pick = (f: IdentityForm) =>
    JSON.stringify([
      f.title,
      f.tagline,
      f.description,
      f.soul,
      f.greeting,
      f.input_placeholder,
      f.starter_questions,
      f.capabilities,
    ]);
  return pick(current) !== pick(drafted);
}

export type FieldErrors = Record<string, string>;

export function validateIdentity(f: IdentityForm): FieldErrors {
  const e: FieldErrors = {};
  const title = f.title.trim();
  if (!title) e.title = "Give your agent a name.";
  else if (title.length > LIMITS.title) e.title = `Keep the name under ${LIMITS.title} characters.`;
  if (f.tagline.length > LIMITS.tagline) e.tagline = `Keep the tagline under ${LIMITS.tagline} characters.`;
  if (f.description.length > LIMITS.description)
    e.description = `Keep the description under ${LIMITS.description} characters.`;
  if (!f.soul.trim()) e.soul = "The persona tells the agent how to behave. It cannot be empty.";
  else if (f.soul.length > LIMITS.soul) e.soul = `Keep the persona under ${LIMITS.soul} characters.`;
  if (f.starter_questions.length > LIMITS.starterQuestions) e.starter_questions = "Use at most 3 starter questions.";
  f.starter_questions.forEach((q, i) => {
    if (!q.label.trim() || !q.prompt.trim()) e[`starter_questions.${i}`] = "Fill in both the label and the question.";
    else if (q.label.length > LIMITS.starterLabel) e[`starter_questions.${i}`] = `Labels are at most ${LIMITS.starterLabel} characters.`;
  });
  f.capabilities.forEach((c, i) => {
    if (!c.name.trim()) e[`capabilities.${i}`] = "Name this capability or remove it.";
    else if (c.name.length > LIMITS.capabilityName || c.description.length > LIMITS.capabilityDescription)
      e[`capabilities.${i}`] = "This capability is too long.";
  });
  return e;
}

/** Parse the pricing form into a wire `Pricing`, or field errors. */
export function parsePricing(p: PricingForm): { pricing: Pricing | null; errors: FieldErrors } {
  const errors: FieldErrors = {};
  const freeRounds = /^\d{1,3}$/.test(p.free_rounds.trim()) ? Number(p.free_rounds.trim()) : null;
  if (p.mode === "per_round" && (freeRounds === null || freeRounds > LIMITS.maxFreeRounds))
    errors.free_rounds = `Enter a whole number from 0 to ${LIMITS.maxFreeRounds}.`;

  let priceCents = 0;
  if (p.mode === "per_round") {
    const parsed = parseCreditsToCents(p.price);
    if (parsed === null) errors.price = "Enter a price like 1.00.";
    else if (parsed < 1) errors.price = "Paid agents need a price of at least 0.01.";
    else if (parsed > LIMITS.maxPriceCents) errors.price = `The maximum price is ${formatCredits(LIMITS.maxPriceCents)}.`;
    else priceCents = parsed;
  }

  let a2aCents = 0;
  if (p.a2a_enabled) {
    const raw = p.a2a_price_touched ? p.a2a_price : p.mode === "per_round" ? p.price : p.a2a_price;
    const parsed = parseCreditsToCents(raw);
    if (parsed === null) errors.a2a_price = "Enter a price like 1.00.";
    else if (parsed > LIMITS.maxPriceCents) errors.a2a_price = `The maximum price is ${formatCredits(LIMITS.maxPriceCents)}.`;
    else a2aCents = parsed;
  } else {
    const parsed = parseCreditsToCents(p.a2a_price);
    a2aCents = parsed !== null && parsed <= LIMITS.maxPriceCents ? parsed : 0;
  }

  if (Object.keys(errors).length > 0) return { pricing: null, errors };
  return {
    pricing: {
      mode: p.mode,
      price_cents: p.mode === "free" ? 0 : priceCents,
      free_rounds: p.mode === "free" ? 0 : (freeRounds ?? 0),
      a2a_enabled: p.a2a_enabled,
      a2a_price_cents: a2aCents,
    },
    errors,
  };
}

/** The effective A2A price string shown in the form (follows the round price until edited). */
export function effectiveA2aPrice(p: PricingForm): string {
  if (p.a2a_price_touched) return p.a2a_price;
  return p.mode === "per_round" ? p.price : p.a2a_price;
}

/**
 * Live pricing sentence, e.g. "Visitors pay 1.00 per round, first 2 rounds free; you receive 0.80 (80%)."
 * Integer cents with the backend's floor (`share = price * bp // 10000`); `bp` comes from meta.
 */
export function pricingLine(pricing: Pricing, revenueShareBp: number): string {
  if (pricing.mode === "free") return "Visitors chat for free. You do not earn from web rounds.";
  const price = formatCredits(pricing.price_cents);
  const share = formatCredits(creatorShareCents(pricing.price_cents, revenueShareBp));
  const free =
    pricing.free_rounds === 0
      ? "no free rounds"
      : pricing.free_rounds === 1
        ? "first round free"
        : `first ${pricing.free_rounds} rounds free`;
  return `Visitors pay ${price} per round, ${free}; you receive ${share} (${formatShareBp(revenueShareBp)}).`;
}

export function a2aLine(pricing: Pricing, revenueShareBp: number): string {
  if (!pricing.a2a_enabled) return "Other AI agents cannot call this agent over A2A.";
  if (pricing.a2a_price_cents === 0) return "Other AI agents call this agent over A2A for free.";
  const share = formatCredits(creatorShareCents(pricing.a2a_price_cents, revenueShareBp));
  return `AI agents pay ${formatCredits(pricing.a2a_price_cents)} per A2A call; you receive ${share}.`;
}

export type SlugStatus =
  | { state: "auto" }
  | { state: "invalid"; message: string }
  | { state: "checking" }
  | { state: "available" }
  | { state: "unavailable"; reason: "slug_invalid" | "slug_reserved" | "slug_taken" | null }
  | { state: "error" };

export function slugLocalCheck(slug: string): SlugStatus | null {
  const s = slug.trim();
  if (!s) return { state: "auto" };
  if (!SLUG_RE.test(s))
    return {
      state: "invalid",
      message: "Use 3–60 lowercase letters, digits and hyphens; start and end with a letter or digit.",
    };
  return null;
}

export const SLUG_REASON_TEXT: Record<"slug_invalid" | "slug_reserved" | "slug_taken", string> = {
  slug_invalid: "This address is not valid.",
  slug_reserved: "This address is reserved. Pick another one.",
  slug_taken: "This address is already taken.",
};

export interface StepContext {
  skillIds: string[];
  drafting: boolean;
  slug: SlugStatus;
}

/** Errors that block leaving `step` (empty = ok). */
export function stepErrors(step: WizardStep, form: WizardForm, ctx: StepContext): FieldErrors {
  switch (step) {
    case "upload":
      return ctx.skillIds.length > 0 ? {} : { upload: "Upload a skill first." };
    case "identity": {
      if (ctx.drafting) return { drafting: "Wait for the draft to finish." };
      const e = validateIdentity(form.identity);
      if (form.records.enabled) {
        const re = validateRecordFields(form.records.fields);
        if (re.length > 0) e.records = re[0];
      }
      return e;
    }
    case "pricing":
      return parsePricing(form.pricing).errors;
    case "golive": {
      const e: FieldErrors = {};
      if (!form.golive.model) e.model = "Pick a model.";
      const s = ctx.slug;
      if (s.state === "invalid") e.slug = s.message;
      else if (s.state === "unavailable") e.slug = s.reason ? SLUG_REASON_TEXT[s.reason] : "This address is not available.";
      else if (s.state === "checking") e.slug = "Checking the address…";
      return e;
    }
  }
}

/** First step (in order) that still has errors before `target`, or null when `target` is reachable. */
export function firstBlockingStep(target: WizardStep, form: WizardForm, ctx: StepContext): WizardStep | null {
  for (const step of WIZARD_STEPS) {
    if (step === target) return null;
    if (Object.keys(stepErrors(step, form, ctx)).length > 0) return step;
  }
  return null;
}

export function nextStep(step: WizardStep): WizardStep | null {
  const i = WIZARD_STEPS.indexOf(step);
  return i >= 0 && i < WIZARD_STEPS.length - 1 ? WIZARD_STEPS[i + 1] : null;
}

export function prevStep(step: WizardStep): WizardStep | null {
  const i = WIZARD_STEPS.indexOf(step);
  return i > 0 ? WIZARD_STEPS[i - 1] : null;
}

export interface CreateAgentRequest {
  skill_ids: string[];
  slug: string | null;
  title: string;
  tagline: string;
  description: string;
  avatar_id: string | null;
  greeting: string;
  input_placeholder: string;
  starter_questions: StarterQuestion[];
  capabilities: Capability[];
  accent_color: AccentColor;
  listed: boolean;
  pricing: Pricing;
  config: {
    soul: string;
    model: string;
    web_search_enabled: boolean;
    record_schema: { version: 1; scope: "session"; fields: RecordField[] } | null;
  };
}

/** Body for `POST /api/agents` (SPEC §6.4 CreateAgentRequest). Throws if the form is invalid. */
export function buildCreateRequest(form: WizardForm, skillIds: string[]): CreateAgentRequest {
  const { pricing, errors } = parsePricing(form.pricing);
  if (!pricing) throw new Error(Object.values(errors)[0] ?? "Pricing is invalid.");
  const id = form.identity;
  const fields = form.records.enabled ? normalizeRecordFields(form.records.fields) : [];
  return {
    skill_ids: skillIds,
    slug: form.golive.slug.trim() || null,
    title: id.title.trim(),
    tagline: id.tagline.trim(),
    description: id.description.trim(),
    avatar_id: id.avatar?.avatar_id ?? null,
    greeting: id.greeting.trim(),
    input_placeholder: id.input_placeholder.trim(),
    starter_questions: id.starter_questions
      .map((q) => ({ label: q.label.trim(), prompt: q.prompt.trim() }))
      .filter((q) => q.label && q.prompt),
    capabilities: id.capabilities
      .map((c) => ({ icon: c.icon, name: c.name.trim(), description: c.description.trim() }))
      .filter((c) => c.name),
    accent_color: id.accent_color,
    listed: form.golive.listed,
    pricing,
    config: {
      soul: id.soul.trim(),
      model: form.golive.model,
      web_search_enabled: form.golive.web_search_enabled,
      record_schema: fields.length > 0 ? { version: 1, scope: "session", fields } : null,
    },
  };
}

/** `52_400` → `"52 s"`, `75_000` → `"1:15"`. */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `${total} s`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** Pricing form prefilled from a stored `Pricing` (agent Settings tab). */
export function pricingFormFromPricing(p: Pricing): PricingForm {
  const price = p.mode === "per_round" ? centsToInput(p.price_cents) : "1.00";
  const a2a = centsToInput(p.a2a_price_cents);
  return {
    mode: p.mode,
    price,
    free_rounds: String(p.free_rounds),
    a2a_enabled: p.a2a_enabled,
    a2a_price: a2a,
    // A stored A2A price that differs from the round price was set on purpose; keep it fixed.
    a2a_price_touched: p.mode === "free" || p.a2a_price_cents !== p.price_cents,
    rationale: "",
  };
}
