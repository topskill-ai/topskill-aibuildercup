/**
 * Visitor-facing price wording (SPEC §11.4). Amounts are integer cents; formatting goes through
 * `formatCredits`, never floats.
 */
import type { PricingMode } from "@/api/types";
import { formatCredits } from "@/lib/money";
import { plural } from "@/lib/format";

export interface PriceInfo {
  mode: PricingMode;
  price_cents: number;
  free_rounds: number;
}

/** Card chip: `"Free"`, `"1.00 / round"`, `"1.00 / round · 2 free"`. */
export function priceChipLabel(p: PriceInfo): string {
  if (p.mode === "free" || p.price_cents <= 0) return "Free";
  const base = `${formatCredits(p.price_cents)} / round`;
  return p.free_rounds > 0 ? `${base} · ${p.free_rounds} free` : base;
}

/** Sentence for the info panel: `"1.00 credits per round, first 2 rounds free."`. */
export function priceSentence(p: PriceInfo): string {
  if (p.mode === "free" || p.price_cents <= 0) return "Free to use.";
  const base = `${formatCredits(p.price_cents)} credits per round`;
  if (p.free_rounds <= 0) return `${base}.`;
  return `${base}, first ${plural(p.free_rounds, "round")} free.`;
}
