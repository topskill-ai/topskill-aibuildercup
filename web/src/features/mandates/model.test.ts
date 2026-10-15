import { describe, expect, it } from "vitest";
import { audienceLabel, callErrorText, effectiveStatus, formatCountdown, spentPercent } from "./model";

const NOW = Date.parse("2026-10-16T08:00:00Z");
const m = (over: Partial<{ status: "active" | "revoked" | "expired"; expires_at: string; spent_cents: number; budget_cents: number }> = {}) => ({
  status: "active" as const,
  expires_at: "2026-10-16T08:30:00Z",
  spent_cents: 400,
  budget_cents: 2000,
  ...over,
});

describe("effectiveStatus", () => {
  it("reports active, revoked, expired and exhausted", () => {
    expect(effectiveStatus(m(), NOW)).toBe("active");
    expect(effectiveStatus(m({ status: "revoked" }), NOW)).toBe("revoked");
    expect(effectiveStatus(m({ status: "expired" }), NOW)).toBe("expired");
    expect(effectiveStatus(m({ expires_at: "2026-10-16T07:59:59Z" }), NOW)).toBe("expired");
    expect(effectiveStatus(m({ spent_cents: 2000 }), NOW)).toBe("exhausted");
  });
});

describe("formatCountdown", () => {
  it("formats minutes, hours and clamps at zero", () => {
    expect(formatCountdown(29 * 60_000 + 59_000)).toBe("29:59");
    expect(formatCountdown(3_723_000)).toBe("1:02:03");
    expect(formatCountdown(-5000)).toBe("0:00");
  });
});

describe("helpers", () => {
  it("computes the spent percentage safely", () => {
    expect(spentPercent(400, 2000)).toBe(20);
    expect(spentPercent(3000, 2000)).toBe(100);
    expect(spentPercent(0, 0)).toBe(0);
  });

  it("labels audiences and call errors", () => {
    expect(audienceLabel("*")).toBe("Any agent");
    expect(audienceLabel("visa-advisor")).toBe("visa-advisor");
    expect(callErrorText("mandate_exceeds_max")).toMatch(/per-call limit/);
    expect(callErrorText("something_new")).toBe("The call did not complete");
    expect(callErrorText(null)).toBe("The call did not complete");
  });
});
