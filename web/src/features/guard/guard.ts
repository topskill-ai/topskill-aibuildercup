/**
 * P2 guard (SPEC §18 "Guard"): pure helpers for Model Armor verdicts and counters.
 */
import type { AgentStats, GuardData, GuardStats } from "@/api/types";

export const EMPTY_GUARD_STATS: GuardStats = {
  prompts_screened: 0,
  prompts_blocked: 0,
  prompts_redacted: 0,
  answers_screened: 0,
  answers_blocked: 0,
  answers_redacted: 0,
  answers_flagged: 0,
  failed_open: 0,
};

export function guardStats(stats: Pick<AgentStats, "guard"> | null | undefined): GuardStats {
  return { ...EMPTY_GUARD_STATS, ...(stats?.guard ?? {}) };
}

/** Sum the counters of several agents (dashboard). */
export function sumGuardStats(list: (Pick<AgentStats, "guard"> | null | undefined)[]): GuardStats {
  const out = { ...EMPTY_GUARD_STATS };
  for (const s of list) {
    const g = guardStats(s);
    for (const k of Object.keys(out) as (keyof GuardStats)[]) out[k] += g[k];
  }
  return out;
}

const CATEGORY_LABELS: Record<string, string> = {
  pi_and_jailbreak: "prompt injection",
  malicious_uris: "malicious link",
  csam: "child safety",
  sdp: "sensitive data",
  rai: "unsafe content",
  "rai:hate_speech": "hate speech",
  "rai:harassment": "harassment",
  "rai:sexually_explicit": "sexual content",
  "rai:dangerous": "dangerous content",
};

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? CATEGORY_LABELS[category.split(":")[0]] ?? category.replace(/[_:]/g, " ");
}

/** Unique, human-readable category list ("prompt injection, dangerous content"). */
export function categoriesText(categories: string[]): string {
  return [...new Set(categories.map(categoryLabel))].join(", ");
}

/** One-line note shown under a screened message. */
export function guardNote(g: GuardData): string {
  const why = categoriesText(g.categories);
  if (g.target === "prompt") {
    return g.action === "blocked"
      ? `Blocked by Model Armor${why ? ` (${why})` : ""}. The agent did not run and you were not charged.`
      : "Sensitive data in your message was hidden from the agent by Model Armor.";
  }
  return g.action === "blocked"
    ? `This answer was withheld by Model Armor${why ? ` (${why})` : ""}. You were not charged.`
    : "Sensitive data in this answer was redacted by Model Armor.";
}
