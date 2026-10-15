/** Pure helpers for the authorise-budget dialog (SPEC §6.4 POST /api/mandates, §9.4). */
import type { Meta } from "@/api/types";
import { formatCredits, parseCreditsToCents } from "@/lib/money";

export const PAYER_AGENT = "enterprise-assistant";
export const INTENT_MAX = 200;
const INTENT_DEFAULT_CHARS = 120;

/** Intent = first 120 chars of the task (SPEC §9.4), whitespace collapsed. */
export function intentFromTask(task: string): string {
  const clean = task.replace(/\s+/g, " ").trim();
  if (!clean) return "Hire specialist agents to help an Acme Corp employee";
  return clean.length <= INTENT_DEFAULT_CHARS ? clean : `${clean.slice(0, INTENT_DEFAULT_CHARS - 1).trimEnd()}…`;
}

export interface BudgetForm {
  intent: string;
  budget: string;
  maxPerCall: string;
  ttl: string;
}

export interface BudgetValidation {
  errors: Partial<Record<keyof BudgetForm, string>>;
  request: {
    audience: string;
    intent: string;
    max_amount_cents: number;
    budget_cents: number;
    ttl_minutes: number;
    payer_agent: string;
  } | null;
}

/** Validate the dialog against the server rules of `POST /api/mandates` and `meta` maxima. */
export function validateBudget(form: BudgetForm, meta: Meta["a2a_mandate_defaults"]): BudgetValidation {
  const errors: BudgetValidation["errors"] = {};
  const intent = form.intent.replace(/\s+/g, " ").trim();
  if (!intent) errors.intent = "Describe what the budget is for.";
  else if (intent.length > INTENT_MAX) errors.intent = `Keep it under ${INTENT_MAX} characters.`;

  const budget = parseCreditsToCents(form.budget);
  const max = parseCreditsToCents(form.maxPerCall);
  if (max === null || max < 1) errors.maxPerCall = "Enter an amount like 5.00.";
  else if (max > 100_000) errors.maxPerCall = `At most ${formatCredits(100_000)}.`;
  if (budget === null || budget < 1) errors.budget = "Enter an amount like 20.00.";
  else if (budget > meta.max_budget_cents) errors.budget = `At most ${formatCredits(meta.max_budget_cents)} credits.`;
  else if (max !== null && budget < max) errors.budget = "The budget must be at least the per-call limit.";

  const ttl = Number(form.ttl);
  if (!Number.isInteger(ttl) || ttl < 1 || ttl > meta.max_ttl_minutes) errors.ttl = "Pick an expiry.";

  const ok = Object.keys(errors).length === 0;
  return {
    errors,
    request: ok
      ? {
          audience: "*",
          intent,
          max_amount_cents: max as number,
          budget_cents: budget as number,
          ttl_minutes: ttl,
          payer_agent: PAYER_AGENT,
        }
      : null,
  };
}

export function ttlOptions(defaultTtl: number, maxTtl: number) {
  const values = Array.from(new Set([5, 15, defaultTtl, 60, 120, 240, 1440])).filter((v) => v >= 1 && v <= maxTtl);
  values.sort((a, b) => a - b);
  return values.map((v) => ({
    value: String(v),
    label: v < 60 ? `${v} minutes` : v % 60 === 0 ? `${v / 60} hour${v === 60 ? "" : "s"}` : `${v} minutes`,
  }));
}

