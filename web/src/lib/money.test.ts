import { describe, expect, it } from "vitest";
import { creatorShareCents, formatCredits, formatCreditsWithUnit, formatShareBp, parseCreditsToCents } from "./money";

describe("formatCredits", () => {
  it("formats integer cents with two decimals and grouping", () => {
    expect(formatCredits(0)).toBe("0.00");
    expect(formatCredits(5)).toBe("0.05");
    expect(formatCredits(100)).toBe("1.00");
    expect(formatCredits(1250)).toBe("12.50");
    expect(formatCredits(123456)).toBe("1,234.56");
  });

  it("uses a real minus sign for negatives and optional plus for positives", () => {
    expect(formatCredits(-40)).toBe("−0.40");
    expect(formatCredits(200, { signed: true })).toBe("+2.00");
    expect(formatCredits(0, { signed: true })).toBe("0.00");
    expect(formatCreditsWithUnit(9900)).toBe("99.00 credits");
  });
});

describe("creatorShareCents", () => {
  it("floors like the backend (paid * bp // 10000)", () => {
    expect(creatorShareCents(100, 8000)).toBe(80);
    expect(creatorShareCents(1, 8000)).toBe(0);
    expect(creatorShareCents(3, 8000)).toBe(2);
    expect(creatorShareCents(199, 7550)).toBe(150);
    expect(creatorShareCents(600, 10000)).toBe(600);
  });

  it("is zero for non-positive inputs", () => {
    expect(creatorShareCents(0, 8000)).toBe(0);
    expect(creatorShareCents(-100, 8000)).toBe(0);
    expect(creatorShareCents(100, 0)).toBe(0);
  });

  it("formats the share percentage", () => {
    expect(formatShareBp(8000)).toBe("80%");
    expect(formatShareBp(7550)).toBe("75.5%");
  });
});

describe("parseCreditsToCents", () => {
  it("parses whole and decimal amounts", () => {
    expect(parseCreditsToCents("1")).toBe(100);
    expect(parseCreditsToCents("1.5")).toBe(150);
    expect(parseCreditsToCents(" 2.05 ")).toBe(205);
    expect(parseCreditsToCents("1,000.25")).toBe(100025);
    expect(parseCreditsToCents("0.")).toBe(0);
  });

  it("rejects invalid input", () => {
    for (const bad of ["", "-1", "1.234", "abc", "1e3", "."]) expect(parseCreditsToCents(bad)).toBeNull();
  });
});
