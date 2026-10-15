import { describe, expect, it } from "vitest";
import { intentFromTask, PAYER_AGENT, ttlOptions, validateBudget, type BudgetForm } from "./budget";

const DEFAULTS = { max_amount_cents: 500, budget_cents: 2000, ttl_minutes: 30, max_budget_cents: 100_000, max_ttl_minutes: 1440 };
const ok: BudgetForm = { intent: "Visa plan for a new hire", budget: "20.00", maxPerCall: "5", ttl: "30" };

describe("validateBudget", () => {
  it("builds the POST /api/mandates body in integer cents", () => {
    const v = validateBudget(ok, DEFAULTS);
    expect(v.errors).toEqual({});
    expect(v.request).toEqual({
      audience: "*",
      intent: "Visa plan for a new hire",
      max_amount_cents: 500,
      budget_cents: 2000,
      ttl_minutes: 30,
      payer_agent: PAYER_AGENT,
    });
  });

  it("rejects a budget below the per-call limit", () => {
    const v = validateBudget({ ...ok, budget: "4.99" }, DEFAULTS);
    expect(v.request).toBeNull();
    expect(v.errors.budget).toMatch(/at least the per-call limit/);
  });

  it("rejects amounts that are not credits with at most two decimals, and zero", () => {
    expect(validateBudget({ ...ok, maxPerCall: "1.234" }, DEFAULTS).errors.maxPerCall).toBeTruthy();
    expect(validateBudget({ ...ok, maxPerCall: "0" }, DEFAULTS).errors.maxPerCall).toBeTruthy();
    expect(validateBudget({ ...ok, budget: "-1" }, DEFAULTS).errors.budget).toBeTruthy();
  });

  it("enforces the server maxima from meta", () => {
    expect(validateBudget({ ...ok, budget: "1000.01" }, DEFAULTS).errors.budget).toMatch(/At most 1,000.00/);
    expect(validateBudget({ ...ok, ttl: "1441" }, DEFAULTS).errors.ttl).toBeTruthy();
  });

  it("requires an intent of at most 200 characters", () => {
    expect(validateBudget({ ...ok, intent: "   " }, DEFAULTS).errors.intent).toBeTruthy();
    expect(validateBudget({ ...ok, intent: "x".repeat(201) }, DEFAULTS).errors.intent).toBeTruthy();
  });
});

describe("intentFromTask", () => {
  it("uses the first 120 characters of the task", () => {
    expect(intentFromTask("  Plan   a visa ")).toBe("Plan a visa");
    const long = intentFromTask("a".repeat(300));
    expect(long.length).toBe(120);
    expect(long.endsWith("…")).toBe(true);
  });

  it("falls back to a generic intent", () => {
    expect(intentFromTask("")).toMatch(/specialist agents/);
  });
});

describe("ttlOptions", () => {
  it("includes the default and never exceeds the maximum", () => {
    const values = ttlOptions(30, 60).map((o) => Number(o.value));
    expect(values).toContain(30);
    expect(Math.max(...values)).toBeLessThanOrEqual(60);
    expect(values).toEqual([...values].sort((a, b) => a - b));
  });
});
