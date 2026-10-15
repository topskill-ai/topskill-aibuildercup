import { describe, expect, it } from "vitest";
import type { BillingStatus, ChatMessage, ChatStreamEvent } from "@/api/types";
import { billingHint } from "./billingHint";
import {
  chargeLabel,
  chatReducer,
  completionAnnouncement,
  fromWire,
  initialChatState,
  toWire,
  type ChatState,
  type ViewMessage,
} from "./chatState";
import { toolSummary } from "./display";

const T = "t_0123456789abcdef";

function run(events: ChatStreamEvent[], state: ChatState = initialChatState): ChatState {
  let s = chatReducer(state, { type: "start", turnId: T, text: "Hello", now: "2026-10-16T08:30:00Z" });
  for (const event of events) s = chatReducer(s, { type: "event", turnId: T, event });
  return s;
}

const model = (s: ChatState) => s.messages.find((m) => m.role === "model") as ViewMessage;

describe("chatReducer", () => {
  it("adds an optimistic user message and a streaming model placeholder", () => {
    const s = run([]);
    expect(s.activeTurnId).toBe(T);
    expect(s.messages.map((m) => [m.role, m.text, Boolean(m.streaming)])).toEqual([
      ["user", "Hello", false],
      ["model", "", true],
    ]);
  });

  it("folds deltas, tools, citations and done into the model message", () => {
    const s = run([
      { event: "status", data: { state: "ready", conversation_id: "cnv_1", turn_id: T, request_id: "turn_ab", model: "m" } },
      { event: "tool_start", data: { id: "c1", tool: "search_knowledge", kind: "search", title: "Searched the knowledge base", target: "fees" } },
      { event: "text_delta", data: { delta: "Fees are " } },
      { event: "tool_end", data: { id: "c1", tool: "search_knowledge", status: "completed" } },
      { event: "tool_start", data: { id: "c2", tool: "save_record", kind: "record", title: "Saved notes", target: "3 fields" } },
      { event: "text_delta", data: { delta: "low [1]." } },
      {
        event: "citations",
        data: {
          sources: [
            { n: 1, kind: "knowledge", title: "visa_guide.pdf", uri: null, domain: null, file_id: "kf_1", page_start: 2, page_end: 2, snippet: "x" },
          ],
          search_entry_point_html: "<div>chips</div>",
          web_search_queries: ["visa fees"],
        },
      },
      {
        event: "done",
        data: {
          conversation_id: "cnv_1",
          message_id: "m_turn_ab",
          turn_id: T,
          outcome: "ok",
          usage: null,
          billing: { charged_cents: 100, free: false, round_index: 3, free_rounds_left: 0, balance_cents: 9900 },
        },
      },
    ]);
    const m = model(s);
    expect(s.activeTurnId).toBeNull();
    expect(m.id).toBe("m_turn_ab");
    expect(m.text).toBe("Fees are low [1].");
    expect(m.streaming).toBe(false);
    expect(m.status).toBe("ok");
    expect(m.citations).toHaveLength(1);
    expect(m.search_entry_point_html).toBe("<div>chips</div>");
    // A tool still running at `done` is closed as an error.
    expect(m.tools.map((t) => [t.id, t.status])).toEqual([
      ["c1", "completed"],
      ["c2", "error"],
    ]);
    expect(m.charge).toEqual({ charged_cents: 100, free: false, round_index: 3 });
    expect(chargeLabel(m, { pricingMode: "per_round", freeRounds: 2 })).toBe("Charged 1.00 · round 3 · balance 99.00");
    expect(s.messages[0].id).toBe("u_turn_ab");
  });

  it("keeps the error and marks running tools interrupted on a synthesized connection loss", () => {
    const s = run([
      { event: "tool_start", data: { id: "w", tool: "google_search", kind: "web", title: "Searched the web", target: "q" } },
      { event: "text_delta", data: { delta: "Partial" } },
      { event: "error", data: { code: "connection_lost", message: "Connection lost", trace_id: "" } },
      { event: "done", data: { conversation_id: null, message_id: null, turn_id: T, outcome: "aborted", usage: null, billing: null } },
    ]);
    const m = model(s);
    expect(m.status).toBe("aborted");
    expect(m.error?.code).toBe("connection_lost");
    expect(m.tools[0].status).toBe("interrupted");
    expect(chargeLabel(m, { pricingMode: "per_round", freeRounds: 0 })).toBe("Connection lost");
  });

  it("cancel drops the optimistic pair; stopped finalizes the turn as aborted", () => {
    const cancelled = chatReducer(run([]), { type: "cancel", turnId: T });
    expect(cancelled.messages).toEqual([]);
    expect(cancelled.activeTurnId).toBeNull();

    const stopped = chatReducer(run([{ event: "text_delta", data: { delta: "Half" } }]), { type: "stopped", turnId: T });
    const m = model(stopped);
    expect(m.status).toBe("aborted");
    expect(m.streaming).toBe(false);
    expect(m.text).toBe("Half");
    expect(chargeLabel(m, { pricingMode: "per_round", freeRounds: 0 })).toBe("Not charged (stopped)");
  });

  it("reset loads server messages and ignores unknown events", () => {
    const wire: ChatMessage = {
      id: "m_1",
      role: "model",
      text: "Hi",
      status: "ok",
      turn_id: "t1",
      citations: [],
      search_entry_point_html: null,
      tools: [],
      charge: { charged_cents: 0, free: true, round_index: 1 },
      created_at: "2026-10-16T08:30:00Z",
    };
    const s = chatReducer(initialChatState, { type: "reset", messages: [wire] });
    expect(s.messages[0].text).toBe("Hi");
    const same = chatReducer(s, { type: "event", turnId: "x", event: { event: "mystery", data: {} } as never });
    expect(same).toBe(s);
  });
});

describe("chargeLabel", () => {
  const base: ViewMessage = {
    id: "m",
    role: "model",
    text: "a",
    status: "ok",
    turn_id: "t",
    citations: [],
    search_entry_point_html: null,
    tools: [],
    charge: null,
    created_at: "",
  };
  const ctx = { pricingMode: "per_round" as const, freeRounds: 2 };

  it("labels free rounds, owner rounds, failures and charged rounds", () => {
    expect(chargeLabel({ ...base, charge: { charged_cents: 0, free: true, round_index: 1 } }, ctx)).toBe("Free round 1 of 2");
    expect(chargeLabel({ ...base, charge: { charged_cents: 0, free: true, round_index: null } }, ctx)).toBe(
      "Free for you (creator)",
    );
    expect(chargeLabel({ ...base, charge: { charged_cents: 0, free: true, round_index: 4 } }, { pricingMode: "free", freeRounds: 0 })).toBe("Free");
    expect(chargeLabel({ ...base, charge: { charged_cents: 100, free: false, round_index: 3 } }, ctx)).toBe("Charged 1.00 · round 3");
    expect(chargeLabel({ ...base, status: "failed" }, ctx)).toBe("Not charged");
    expect(chargeLabel({ ...base, streaming: true }, ctx)).toBeNull();
    expect(chargeLabel({ ...base, role: "user" }, ctx)).toBeNull();
  });

  it("keeps an owner round free for the creator through the session stash round trip", () => {
    const owner: ViewMessage = { ...base, charge: { charged_cents: 0, free: true, round_index: null } };
    const restored = fromWire(JSON.parse(JSON.stringify(toWire(owner))) as ChatMessage);
    expect(restored.charge?.round_index).toBeNull();
    expect(chargeLabel(restored, ctx)).toBe("Free for you (creator)");
    expect(chargeLabel(restored, { pricingMode: "per_round", freeRounds: 0 })).toBe("Free for you (creator)");
    // A stash written by an older build (round_index 0) still reads as the owner.
    expect(chargeLabel({ ...base, charge: { charged_cents: 0, free: true, round_index: 0 } }, ctx)).toBe("Free for you (creator)");
  });
});

describe("completionAnnouncement", () => {
  const ctx = { pricingMode: "per_round" as const, freeRounds: 2 };
  it("announces only turns that finished live, with their charge", () => {
    const live = run([
      { event: "text_delta", data: { delta: "Hi" } },
      {
        event: "done",
        data: {
          conversation_id: "cnv_1",
          message_id: "m_1",
          turn_id: T,
          outcome: "ok",
          usage: null,
          billing: { charged_cents: 100, free: false, round_index: 3, free_rounds_left: 0, balance_cents: 900 },
        },
      },
    ]);
    expect(completionAnnouncement(live.messages, ctx)).toBe("Answer ready · Charged 1.00 · round 3 · balance 9.00");
    expect(completionAnnouncement(run([]).messages, ctx)).toBe("");
    const stopped = chatReducer(run([]), { type: "stopped", turnId: T });
    expect(completionAnnouncement(stopped.messages, ctx)).toBe("Answer stopped · Not charged (stopped)");
    const restored = chatReducer(initialChatState, { type: "reset", messages: live.messages.map(toWire) });
    expect(completionAnnouncement(restored.messages, ctx)).toBe("");
  });
});

describe("billingHint", () => {
  const status = (over: Partial<BillingStatus>): BillingStatus => ({
    billing_mode: "per_round",
    price_cents: 100,
    free_rounds: 2,
    is_owner: false,
    used_rounds: 0,
    free_rounds_left: 2,
    next_round_charge_cents: 0,
    balance_cents: 10000,
    can_afford_next_round: true,
    owner_available: true,
    ...over,
  });
  const pricing = { mode: "per_round" as const, price_cents: 100, free_rounds: 2 };

  it("covers signed-out, free rounds left, owner, broke and paying visitors", () => {
    expect(billingHint(null, pricing).text).toBe("1.00 per round · first 2 rounds free · sign in to chat");
    expect(billingHint(status({}), pricing).text).toBe("1.00 per round · 2 free rounds left");
    expect(billingHint(status({ is_owner: true }), pricing).text).toBe("You're the creator — free for you");
    const broke = billingHint(
      status({ free_rounds_left: 0, balance_cents: 40, next_round_charge_cents: 100, can_afford_next_round: false }),
      pricing,
    );
    expect(broke).toEqual({ tone: "warning", text: "Not enough credits: need 1.00, you have 0.40", topUp: true });
    expect(billingHint(status({ free_rounds_left: 0, balance_cents: 9900 }), pricing).text).toBe("1.00 per round · balance 99.00");
    expect(billingHint(status({ billing_mode: "free", price_cents: 0 }), pricing).text).toBe("Free to use");
  });

  it("never asks a signed-in visitor to sign in while the status loads or after it failed", () => {
    expect(billingHint(undefined, pricing, { signedIn: true, loading: true }).text).toBe(
      "1.00 per round · first 2 rounds free · checking your balance…",
    );
    expect(billingHint(undefined, pricing, { signedIn: true }).text).toBe("1.00 per round · first 2 rounds free");
    expect(billingHint(undefined, { mode: "free", price_cents: 0, free_rounds: 0 }, { signedIn: true }).text).toBe("Free to use");
  });
});

describe("toolSummary", () => {
  it("joins unique titles in order", () => {
    const line = (id: string, title: string) => ({ id, tool: id, kind: "other" as const, title, target: "", status: "completed" as const });
    expect(toolSummary([line("a", "Searched the knowledge base"), line("b", "Searched the web"), line("c", "Searched the web"), line("d", "Saved notes")])).toBe(
      "Searched the knowledge base · Searched the web · Saved notes",
    );
  });
});
