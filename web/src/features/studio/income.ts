/** Creator income helpers (dashboard tiles, "Income by channel" bar, new-row detection). */
import type { LedgerCategory, LedgerEntry } from "@/api/types";

export type IncomeChannel = "visitor" | "a2a" | "other";

export function incomeChannel(category: LedgerCategory): IncomeChannel {
  if (category === "creator_income") return "visitor";
  if (category === "a2a_income") return "a2a";
  return "other";
}

export interface ChannelSplit {
  visitorCents: number;
  a2aCents: number;
  totalCents: number;
  /** Integer percentages that always add up to 100 when total > 0. */
  visitorPct: number;
  a2aPct: number;
}

export function channelSplit(visitorCents: number, a2aCents: number): ChannelSplit {
  const v = Math.max(0, Math.trunc(visitorCents));
  const a = Math.max(0, Math.trunc(a2aCents));
  const total = v + a;
  if (total === 0) return { visitorCents: v, a2aCents: a, totalCents: 0, visitorPct: 0, a2aPct: 0 };
  const visitorPct = Math.round((v * 100) / total);
  return { visitorCents: v, a2aCents: a, totalCents: total, visitorPct, a2aPct: 100 - visitorPct };
}

/** Ids in `next` that were not in `previous` (empty on the first load, so nothing flashes). */
export function newEntryIds(previous: LedgerEntry[] | undefined, next: LedgerEntry[]): Set<string> {
  if (!previous) return new Set();
  const seen = new Set(previous.map((e) => e.id));
  return new Set(next.filter((e) => !seen.has(e.id)).map((e) => e.id));
}
