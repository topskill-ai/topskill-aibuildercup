import { describe, expect, it } from "vitest";
import { chartData, columnLabel, formatBytes, formatCell, niceTicks } from "./format";
import type { AnalyticsAnswer } from "./types";

function answer(over: Partial<AnalyticsAnswer> = {}): AnalyticsAnswer {
  return {
    question: "q",
    outcome: "answered",
    sql: "SELECT 1",
    columns: [
      { name: "agent_title", type: "STRING" },
      { name: "income_cents", type: "INTEGER" },
    ],
    rows: [
      ["Visa Advisor", 600],
      ["Resume Reviewer", 160],
    ],
    answer: "Visa Advisor earned the most.",
    chart_hint: { type: "bar", x: "agent_title", y: "income_cents" },
    truncated: false,
    bytes_processed: 2048,
    model: "gemini-3.8-flash",
    data_as_of: "2026-10-16T08:30:00Z",
    ...over,
  };
}

describe("columnLabel", () => {
  it("turns snake_case into labels and marks money columns", () => {
    expect(columnLabel("income_cents")).toBe("Income (credits)");
    expect(columnLabel("a2a_calls")).toBe("A2A calls");
    expect(columnLabel("day")).toBe("Day");
  });
});

describe("formatCell", () => {
  it("formats cents, numbers, booleans, arrays, timestamps and nulls", () => {
    expect(formatCell(160, { name: "income_cents", type: "INTEGER" })).toBe("1.60");
    expect(formatCell(1234, { name: "rounds", type: "INTEGER" })).toBe("1,234");
    expect(formatCell(0.456, { name: "rate", type: "FLOAT" })).toBe("0.46");
    expect(formatCell(true, { name: "listed", type: "BOOLEAN" })).toBe("Yes");
    expect(formatCell(["a", "b"], { name: "filled_keys", type: "STRING[]" })).toBe("a, b");
    expect(formatCell(null, { name: "x", type: "STRING" })).toBe("—");
    expect(formatCell("2026-10-16", { name: "day", type: "DATE" })).toBe("Oct 16, 2026");
    // a string column named *_cents is not money
    expect(formatCell("abc", { name: "note_cents", type: "STRING" })).toBe("abc");
  });
});

describe("chartData", () => {
  it("builds a money bar series from the hint", () => {
    const c = chartData(answer());
    expect(c).toEqual({
      type: "bar",
      xLabel: "Agent title",
      yLabel: "Income (credits)",
      money: true,
      points: [
        { label: "Visa Advisor", value: 600 },
        { label: "Resume Reviewer", value: 160 },
      ],
    });
  });

  it("returns null when the hint does not fit", () => {
    expect(chartData(answer({ chart_hint: { type: "none", x: null, y: null } }))).toBeNull();
    expect(chartData(answer({ chart_hint: { type: "bar", x: "agent_title", y: "missing" } }))).toBeNull();
    expect(chartData(answer({ chart_hint: { type: "bar", x: "income_cents", y: "agent_title" } }))).toBeNull();
    expect(chartData(answer({ rows: [["Visa", 1]] }))).toBeNull();
  });
});

describe("helpers", () => {
  it("formats bytes and nice ticks", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
    expect(niceTicks(600)).toEqual([0, 200, 400, 600]);
    expect(niceTicks(0)).toEqual([0, 1]);
    const t = niceTicks(7);
    expect(t[0]).toBe(0);
    expect(t[t.length - 1]).toBeGreaterThanOrEqual(7);
  });
});
