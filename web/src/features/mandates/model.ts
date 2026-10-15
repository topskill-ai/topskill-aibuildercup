/**
 * Pure helpers for mandates and A2A call outcomes (no React), shared by the wallet and assistant.
 */
import { useEffect, useState } from "react";
import type { Mandate } from "@/api/types";

export type EffectiveMandateStatus = "active" | "revoked" | "expired" | "exhausted";

/** Server status, plus `expired` once `expires_at` passed and `exhausted` when no budget is left. */
export function effectiveStatus(
  m: Pick<Mandate, "status" | "expires_at" | "spent_cents" | "budget_cents">,
  now: number,
): EffectiveMandateStatus {
  if (m.status === "revoked") return "revoked";
  if (m.status === "expired") return "expired";
  const exp = Date.parse(m.expires_at);
  if (Number.isFinite(exp) && exp <= now) return "expired";
  if (m.spent_cents >= m.budget_cents) return "exhausted";
  return "active";
}

/** `"29:59"`, `"1:02:03"`, `"0:00"` for a remaining duration in ms. */
export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** `"*"` → "Any agent", otherwise the slug. */
export function audienceLabel(audience: string): string {
  return audience === "*" ? "Any agent" : audience;
}

/** Percentage (0–100) of the budget already spent; clamps and handles a zero budget. */
export function spentPercent(spentCents: number, budgetCents: number): number {
  if (budgetCents <= 0) return spentCents > 0 ? 100 : 0;
  return Math.min(100, Math.max(0, (spentCents / budgetCents) * 100));
}

/** User-facing reason for an A2A call that was rejected or failed (§6.2 assistant codes). */
export const CALL_ERROR_TEXT: Record<string, string> = {
  mandate_exceeds_max: "Price is above the per-call limit you authorised",
  mandate_budget_exhausted: "Not enough budget left on the mandate",
  mandate_expired: "The mandate has expired",
  mandate_revoked: "The mandate was revoked",
  mandate_audience: "The mandate does not cover this agent",
  mandate_invalid: "The mandate could not be verified",
  mandate_missing: "No mandate was attached",
  mandate_replay: "This message was already paid for",
  payer_insufficient_points: "Your wallet balance is too low",
  insufficient_points: "The agent is temporarily unavailable",
  agent_offline: "The agent is offline",
  agent_not_found: "The agent was not found",
  call_limit: "Call limit for one request reached",
  upstream_error: "The agent could not be reached",
  agent_run_failed: "The agent failed to answer",
  validation_error: "The request was not valid",
  canceled: "The call was canceled",
};

export function callErrorText(code: string | null | undefined): string {
  if (!code) return "The call did not complete";
  return CALL_ERROR_TEXT[code] ?? "The call did not complete";
}

/** Current time that re-renders every `intervalMs` (countdowns). */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}
