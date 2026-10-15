/** Short display labels for agents in studio tables and headers. */
import type { Pricing } from "@/api/types";
import { formatCredits } from "@/lib/money";

/** `"1.00 / round · 2 free"`, `"Free"`. */
export function priceLabel(p: Pricing): string {
  if (p.mode === "free") return "Free";
  const free = p.free_rounds > 0 ? ` · ${p.free_rounds} free` : "";
  return `${formatCredits(p.price_cents)} / round${free}`;
}

/** `"A2A 2.00 / call"`, `"A2A off"`. */
export function a2aPriceLabel(p: Pricing): string {
  if (!p.a2a_enabled) return "A2A off";
  return p.a2a_price_cents === 0 ? "A2A free" : `A2A ${formatCredits(p.a2a_price_cents)} / call`;
}
