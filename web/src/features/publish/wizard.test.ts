import { describe, expect, it } from "vitest";
import type { AgentDraft } from "@/api/types";
import {
  a2aLine,
  buildCreateRequest,
  effectiveA2aPrice,
  emptyForm,
  firstBlockingStep,
  formatElapsed,
  formFromDraft,
  identityEdited,
  nextStep,
  parsePricing,
  prevStep,
  pricingLine,
  slugLocalCheck,
  stepErrors,
  templateDraft,
  type StepContext,
  type WizardForm,
} from "./wizard";

const draft: AgentDraft = {
  title: "Visa Advisor",
  tagline: "Work visa plans in minutes",
  description: "Helps employers plan work visas.",
  soul: "You are a visa advisor. First ask about nationality, then explain options.",
  greeting: "Hi, I'm Visa Advisor. Where is the new hire from?",
  input_placeholder: "Describe the hire…",
  starter_questions: [
    { label: "Brazil to Germany", prompt: "Plan a work visa for a hire from Brazil moving to Germany." },
    { label: "Costs", prompt: "What does a work visa cost?" },
    { label: "Timeline", prompt: "How long does it take?" },
  ],
  capabilities: [{ icon: "plane", name: "Visa routes", description: "Compare visa routes." }],
  accent_color: "emerald",
  pricing: { mode: "per_round", price_cents: 150, free_rounds: 2, rationale: "Expert advice" },
  record_fields: [
    {
      key: "nationality",
      label: "Nationality",
      type: "text",
      description: "",
      options: null,
      required: true,
      pii: false,
      show_in_table: true,
      min: null,
      max: null,
    },
  ],
  web_search_recommended: false,
  avatar_prompt: "A passport and a globe",
  model_used: "gemini-3.8-flash",
};

const ok: StepContext = { skillIds: ["skl_1"], drafting: false, slug: { state: "auto" } };

function drafted(): WizardForm {
  return formFromDraft(draft, emptyForm("gemini-3.8-flash"));
}

describe("formFromDraft", () => {
  it("prefills identity, pricing, records and web search from the draft", () => {
    const f = drafted();
    expect(f.identity.title).toBe("Visa Advisor");
    expect(f.identity.accent_color).toBe("emerald");
    expect(f.identity.starter_questions).toHaveLength(3);
    expect(f.pricing).toMatchObject({ mode: "per_round", price: "1.50", free_rounds: "2", a2a_price: "1.50" });
    expect(f.records.enabled).toBe(true);
    expect(f.records.fields[0].key).toBe("nationality");
    expect(f.golive).toMatchObject({ model: "gemini-3.8-flash", web_search_enabled: false });
  });

  it("keeps an avatar the creator already picked and clips overlong values", () => {
    const prev = emptyForm("m");
    prev.identity.avatar = { avatar_id: "av_1", avatar_url: "https://x/av_1" };
    const f = formFromDraft({ ...draft, title: "x".repeat(60) }, prev);
    expect(f.identity.avatar?.avatar_id).toBe("av_1");
    expect(f.identity.title).toHaveLength(40);
    expect(f.identity.title.endsWith("…")).toBe(true);
  });

  it("detects edits for the Redraft confirmation", () => {
    const f = drafted();
    expect(identityEdited(f.identity, f.identity)).toBe(false);
    expect(identityEdited({ ...f.identity, title: "Other" }, f.identity)).toBe(true);
    expect(identityEdited(f.identity, null)).toBe(false);
  });

  it("template prefill is a valid, publishable starting point", () => {
    const f = formFromDraft(templateDraft([{ name: "contract-reviewer", description: "Reviews offer letters." }]), emptyForm("m"));
    expect(f.identity.title).toBe("Contract Reviewer");
    expect(stepErrors("identity", f, ok)).toEqual({});
  });
});

describe("step validation", () => {
  it("upload needs a skill", () => {
    expect(stepErrors("upload", emptyForm("m"), { ...ok, skillIds: [] })).toHaveProperty("upload");
    expect(stepErrors("upload", emptyForm("m"), ok)).toEqual({});
  });

  it("identity requires a title and persona, and blocks while drafting", () => {
    const f = emptyForm("m");
    const e = stepErrors("identity", f, ok);
    expect(e).toHaveProperty("title");
    expect(e).toHaveProperty("soul");
    expect(stepErrors("identity", drafted(), { ...ok, drafting: true })).toHaveProperty("drafting");
    expect(stepErrors("identity", drafted(), ok)).toEqual({});
  });

  it("identity flags invalid record fields only when lead capture is on", () => {
    const f = drafted();
    f.records.fields = [{ ...f.records.fields[0], label: "" }];
    expect(stepErrors("identity", f, ok)).toHaveProperty("records");
    f.records.enabled = false;
    expect(stepErrors("identity", f, ok)).toEqual({});
  });

  it("go live blocks on invalid, taken or still-checking slugs and missing model", () => {
    const f = drafted();
    expect(stepErrors("golive", f, ok)).toEqual({});
    expect(stepErrors("golive", f, { ...ok, slug: { state: "checking" } })).toHaveProperty("slug");
    expect(stepErrors("golive", f, { ...ok, slug: { state: "unavailable", reason: "slug_taken" } }).slug).toMatch(
      /taken/,
    );
    expect(stepErrors("golive", { ...f, golive: { ...f.golive, model: "" } }, ok)).toHaveProperty("model");
  });

  it("firstBlockingStep finds the earliest step that is not complete", () => {
    const f = drafted();
    expect(firstBlockingStep("golive", f, ok)).toBeNull();
    expect(firstBlockingStep("golive", f, { ...ok, skillIds: [] })).toBe("upload");
    const badPrice = { ...f, pricing: { ...f.pricing, price: "abc" } };
    expect(firstBlockingStep("golive", badPrice, ok)).toBe("pricing");
    expect(firstBlockingStep("pricing", badPrice, ok)).toBeNull();
  });

  it("step order", () => {
    expect(nextStep("upload")).toBe("identity");
    expect(nextStep("golive")).toBeNull();
    expect(prevStep("pricing")).toBe("identity");
    expect(prevStep("upload")).toBeNull();
  });

  it("slug local check", () => {
    expect(slugLocalCheck("")).toEqual({ state: "auto" });
    expect(slugLocalCheck("Visa Advisor")?.state).toBe("invalid");
    expect(slugLocalCheck("-visa")?.state).toBe("invalid");
    expect(slugLocalCheck("visa-advisor")).toBeNull();
  });
});

describe("pricing", () => {
  it("parses credits into integer cents", () => {
    const { pricing } = parsePricing({ ...emptyForm("m").pricing, price: "1.5", free_rounds: "2" });
    expect(pricing).toEqual({ mode: "per_round", price_cents: 150, free_rounds: 2, a2a_enabled: true, a2a_price_cents: 150 });
  });

  it("A2A price follows the round price until edited", () => {
    const p = { ...emptyForm("m").pricing, price: "2.00" };
    expect(effectiveA2aPrice(p)).toBe("2.00");
    const edited = { ...p, a2a_price: "6.00", a2a_price_touched: true };
    expect(effectiveA2aPrice(edited)).toBe("6.00");
    expect(parsePricing(edited).pricing?.a2a_price_cents).toBe(600);
  });

  it("rejects zero, negative, malformed and too-precise prices", () => {
    const p = emptyForm("m").pricing;
    expect(parsePricing({ ...p, price: "0" }).errors).toHaveProperty("price");
    expect(parsePricing({ ...p, price: "-1" }).errors).toHaveProperty("price");
    expect(parsePricing({ ...p, price: "1.005" }).errors).toHaveProperty("price");
    expect(parsePricing({ ...p, price: "2000" }).errors).toHaveProperty("price");
    expect(parsePricing({ ...p, free_rounds: "1.5" }).errors).toHaveProperty("free_rounds");
    expect(parsePricing({ ...p, free_rounds: "101" }).errors).toHaveProperty("free_rounds");
  });

  it("free mode forces price and free rounds to 0", () => {
    const { pricing } = parsePricing({ ...emptyForm("m").pricing, mode: "free", price: "junk", free_rounds: "x" });
    expect(pricing).toMatchObject({ mode: "free", price_cents: 0, free_rounds: 0 });
  });

  it("pricing line uses meta.revenue_share_bp with the backend floor", () => {
    const pricing = { mode: "per_round" as const, price_cents: 100, free_rounds: 2, a2a_enabled: true, a2a_price_cents: 200 };
    expect(pricingLine(pricing, 8000)).toBe("Visitors pay 1.00 per round, first 2 rounds free; you receive 0.80 (80%).");
    expect(pricingLine({ ...pricing, price_cents: 1, free_rounds: 0 }, 8000)).toBe(
      "Visitors pay 0.01 per round, no free rounds; you receive 0.00 (80%).",
    );
    expect(pricingLine({ ...pricing, price_cents: 99, free_rounds: 1 }, 7550)).toBe(
      "Visitors pay 0.99 per round, first round free; you receive 0.74 (75.5%).",
    );
    expect(pricingLine({ ...pricing, mode: "free" }, 8000)).toMatch(/free/);
    expect(a2aLine(pricing, 8000)).toBe("AI agents pay 2.00 per A2A call; you receive 1.60.");
    expect(a2aLine({ ...pricing, a2a_enabled: false }, 8000)).toMatch(/cannot/);
  });
});

describe("buildCreateRequest", () => {
  it("builds the §6.4 CreateAgentRequest", () => {
    const f = drafted();
    f.identity.avatar = { avatar_id: "av_9", avatar_url: "u" };
    f.identity.starter_questions.push({ label: " ", prompt: "" });
    const req = buildCreateRequest(f, ["skl_1"]);
    expect(req).toMatchObject({
      skill_ids: ["skl_1"],
      slug: null,
      title: "Visa Advisor",
      avatar_id: "av_9",
      accent_color: "emerald",
      listed: true,
      pricing: { mode: "per_round", price_cents: 150, free_rounds: 2, a2a_enabled: true, a2a_price_cents: 150 },
      config: { model: "gemini-3.8-flash", web_search_enabled: false },
    });
    expect(req.starter_questions).toHaveLength(3);
    expect(req.config.record_schema?.fields.map((x) => x.key)).toEqual(["nationality"]);
    expect(req).not.toHaveProperty("temperature");
  });

  it("sends a custom slug and null record schema when lead capture is off", () => {
    const f = drafted();
    f.golive.slug = "visa-advisor";
    f.records.enabled = false;
    const req = buildCreateRequest(f, ["skl_1"]);
    expect(req.slug).toBe("visa-advisor");
    expect(req.config.record_schema).toBeNull();
  });

  it("throws on invalid pricing", () => {
    const f = drafted();
    f.pricing.price = "nope";
    expect(() => buildCreateRequest(f, ["skl_1"])).toThrow();
  });
});

describe("formatElapsed", () => {
  it("formats the wizard timer", () => {
    expect(formatElapsed(0)).toBe("0 s");
    expect(formatElapsed(52_400)).toBe("52 s");
    expect(formatElapsed(75_000)).toBe("1:15");
  });
});
