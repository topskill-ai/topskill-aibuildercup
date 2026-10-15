import { describe, expect, it } from "vitest";
import type { Agent } from "@/api/types";
import {
  profileFromAgent,
  profilePatch,
  runtimeFromAgent,
  runtimePatch,
  temperatureOptions,
  validateProfile,
  validateRuntime,
} from "./settings";

const agent = {
  id: "agt_1",
  slug: "visa-advisor",
  title: "Visa Advisor",
  tagline: "Plans",
  description: "",
  avatar_id: null,
  avatar_url: null,
  greeting: "Hi",
  input_placeholder: "",
  starter_questions: null,
  capabilities: [{ icon: "plane", name: "Routes", description: "" }],
  accent_color: "blue",
  listed: true,
  creator_name: "Ana",
  creator_headline: "",
  pricing: { mode: "per_round", price_cents: 100, free_rounds: 2, a2a_enabled: true, a2a_price_cents: 100 },
  config: {
    soul: "You are a visa advisor.",
    model: "gemini-3.8-flash",
    temperature: null,
    web_search_enabled: true,
    skill_ids: ["skl_1"],
    record_schema: {
      version: 1,
      scope: "session",
      fields: [
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
    },
  },
} as unknown as Agent;

describe("profile patch", () => {
  it("sends nothing when unchanged and only changed fields otherwise", () => {
    const p0 = profileFromAgent(agent);
    expect(profilePatch(p0, profileFromAgent(agent))).toEqual({});
    const p = { ...p0, title: " Visa Pro ", listed: false, avatar: { avatar_id: "av_1", avatar_url: "u" } };
    expect(profilePatch(p0, p)).toEqual({ title: "Visa Pro", listed: false, avatar_id: "av_1" });
  });

  it("keeps automatic starter questions (null) until edited, and drops blank rows", () => {
    const p0 = profileFromAgent(agent);
    expect(p0.starter_questions).toBeNull();
    const edited = { ...p0, starter_questions: [{ label: "Costs", prompt: "What does it cost?" }, { label: "", prompt: "" }] };
    expect(profilePatch(p0, edited)).toEqual({ starter_questions: [{ label: "Costs", prompt: "What does it cost?" }] });
    expect(profilePatch(p0, { ...p0, starter_questions: [] })).toEqual({ starter_questions: [] });
  });

  it("validates the name and partial rows", () => {
    const p0 = profileFromAgent(agent);
    expect(validateProfile(p0)).toEqual({});
    expect(validateProfile({ ...p0, title: " " })).toHaveProperty("title");
    expect(validateProfile({ ...p0, capabilities: [{ icon: "x", name: "", description: "d" }] })).toHaveProperty("capabilities.0");
  });
});

describe("runtime patch", () => {
  it("sends only changed config fields", () => {
    const r0 = runtimeFromAgent(agent);
    expect(runtimePatch(r0, runtimeFromAgent(agent))).toEqual({});
    expect(runtimePatch(r0, { ...r0, model: "gemini-2.5-pro", temperature: 0.7 })).toEqual({
      config: { model: "gemini-2.5-pro", temperature: 0.7 },
    });
  });

  it("turning lead capture off sends record_schema null", () => {
    const r0 = runtimeFromAgent(agent);
    expect(r0.records_enabled).toBe(true);
    expect(runtimePatch(r0, { ...r0, records_enabled: false })).toEqual({ config: { record_schema: null } });
  });

  it("validates persona and record fields", () => {
    const r0 = runtimeFromAgent(agent);
    expect(validateRuntime(r0)).toEqual({});
    expect(validateRuntime({ ...r0, soul: "" })).toHaveProperty("soul");
    expect(validateRuntime({ ...r0, record_fields: [{ ...r0.record_fields[0], key: "Bad Key" }] })).toHaveProperty("records");
  });

  it("offers 0.7 only on gemini-2.5 models", () => {
    expect(temperatureOptions("gemini-3.8-flash", null).map((o) => o.value)).toEqual([""]);
    expect(temperatureOptions("gemini-2.5-pro", null).map((o) => o.value)).toEqual(["", "0.7"]);
    expect(temperatureOptions("gemini-3.8-flash", 0.3).map((o) => o.value)).toEqual(["", "0.3"]);
  });
});
