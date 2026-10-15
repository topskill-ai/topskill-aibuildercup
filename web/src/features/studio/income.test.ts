import { describe, expect, it } from "vitest";
import type { LedgerEntry } from "@/api/types";
import { channelSplit, incomeChannel, newEntryIds } from "./income";

const entry = (id: string): LedgerEntry =>
  ({ id, category: "creator_income", amount_cents: 80, memo: "", created_at: "2026-10-16T08:00:00Z" }) as LedgerEntry;

describe("income helpers", () => {
  it("maps categories to channels", () => {
    expect(incomeChannel("creator_income")).toBe("visitor");
    expect(incomeChannel("a2a_income")).toBe("a2a");
    expect(incomeChannel("signup_grant")).toBe("other");
  });

  it("splits income into percentages that add up to 100", () => {
    expect(channelSplit(0, 0)).toMatchObject({ totalCents: 0, visitorPct: 0, a2aPct: 0 });
    expect(channelSplit(240, 160)).toMatchObject({ totalCents: 400, visitorPct: 60, a2aPct: 40 });
    const odd = channelSplit(1, 2);
    expect(odd.visitorPct + odd.a2aPct).toBe(100);
  });

  it("detects new ledger rows only after the first load", () => {
    expect(newEntryIds(undefined, [entry("a")]).size).toBe(0);
    expect([...newEntryIds([entry("a")], [entry("b"), entry("a")])]).toEqual(["b"]);
  });
});
