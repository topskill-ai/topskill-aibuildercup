/**
 * Text of the billing hint above the composer (SPEC §11.4), derived from `BillingStatus` when the
 * visitor is signed in, or from the public pricing when not.
 */
import type { BillingStatus, PricingMode } from "@/api/types";
import { formatCredits } from "@/lib/money";
import { plural } from "@/lib/format";

export type BillingHintTone = "neutral" | "success" | "warning";

export interface BillingHintModel {
  tone: BillingHintTone;
  text: string;
  /** Show a "Top up" action (visitor cannot afford the next round). */
  topUp: boolean;
}

export interface PublicPricing {
  mode: PricingMode;
  price_cents: number;
  free_rounds: number;
}

export interface BillingHintOptions {
  /** The visitor is signed in (status may still be loading or have failed). */
  signedIn?: boolean;
  /** The signed-in billing status is still loading. */
  loading?: boolean;
}

export function billingHint(
  status: BillingStatus | null | undefined,
  pricing: PublicPricing,
  { signedIn = false, loading = false }: BillingHintOptions = {},
): BillingHintModel {
  if (!status) {
    // Public pricing: signed out, or signed in while `GET /billing` loads or after it failed.
    const suffix = !signedIn ? " · sign in to chat" : loading ? " · checking your balance…" : "";
    if (pricing.mode === "free" || pricing.price_cents <= 0) {
      return { tone: "success", text: `Free to use${suffix}`, topUp: false };
    }
    const price = `${formatCredits(pricing.price_cents)} per round`;
    const free = pricing.free_rounds > 0 ? ` · first ${plural(pricing.free_rounds, "round")} free` : "";
    return { tone: "neutral", text: `${price}${free}${suffix}`, topUp: false };
  }
  if (status.is_owner) return { tone: "success", text: "You're the creator — free for you", topUp: false };
  if (status.billing_mode === "free" || status.price_cents <= 0) {
    return { tone: "success", text: "Free to use", topUp: false };
  }
  const price = `${formatCredits(status.price_cents)} per round`;
  const freeLeft = status.free_rounds_left ?? 0;
  if (freeLeft > 0) {
    return { tone: "success", text: `${price} · ${plural(freeLeft, "free round")} left`, topUp: false };
  }
  if (!status.can_afford_next_round) {
    return {
      tone: "warning",
      text: `Not enough credits: need ${formatCredits(status.next_round_charge_cents || status.price_cents)}, you have ${formatCredits(status.balance_cents)}`,
      topUp: true,
    };
  }
  return { tone: "neutral", text: `${price} · balance ${formatCredits(status.balance_cents)}`, topUp: false };
}
