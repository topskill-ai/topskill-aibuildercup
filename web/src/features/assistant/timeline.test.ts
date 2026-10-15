import { describe, expect, it } from "vitest";
import type { A2AResultData, AssistantStreamEvent, DoneData } from "@/api/types";
import {
  applyCounterEvent,
  assistantReducer,
  buildHistory,
  initialAssistantState,
  summarizeTurn,
  type AssistantState,
  type CallItem,
  type DiscoverItem,
  type MandateCounters,
  type Turn,
} from "./timeline";

const T = "turn-1";
const MANDATE: MandateCounters = { id: "mdt_1", spentCents: 0, budgetCents: 2000, calls: 0, payerBalanceCents: null };

function start(text = "Plan a work visa for a new hire"): AssistantState {
  let s = assistantReducer(initialAssistantState, { type: "mandate_set", counters: MANDATE });
  s = assistantReducer(s, { type: "turn_started", turnId: T, text, now: 1 });
  return s;
}

function feed(state: AssistantState, events: AssistantStreamEvent[], turnId = T): AssistantState {
  return events.reduce((s, event, i) => assistantReducer(s, { type: "stream_event", turnId, event, now: 10 + i }), state);
}

function turnOf(state: AssistantState, id = T): Turn {
  const t = state.turns.find((x) => x.id === id);
  if (!t) throw new Error("turn missing");
  return t;
}

function calls(state: AssistantState): CallItem[] {
  return turnOf(state).items.filter((i): i is CallItem => i.kind === "call");
}

function result(partial: Partial<A2AResultData> & Pick<A2AResultData, "slug" | "status">): AssistantStreamEvent {
  return {
    event: "a2a_result",
    data: {
      call_id: null,
      charged_cents: 0,
      creator_share_cents: 0,
      answer_preview: "",
      error_code: null,
      payer_balance_cents: 9600,
      mandate_spent_cents: 0,
      mandate_budget_cents: 2000,
      ...partial,
    },
  };
}

function done(outcome: DoneData["outcome"], mandate?: { spent: number; calls: number }): AssistantStreamEvent {
  const data = {
    conversation_id: null,
    message_id: null,
    turn_id: T,
    outcome,
    usage: null,
    billing: null,
    ...(mandate ? { mandate: { id: "mdt_1", spent_cents: mandate.spent, budget_cents: 2000, calls: mandate.calls } } : {}),
  };
  return { event: "done", data } as AssistantStreamEvent;
}

const status: AssistantStreamEvent = {
  event: "status",
  data: { state: "ready", conversation_id: null, turn_id: T, request_id: "r1", model: "gemini-3.8-flash" },
};

const discover: AssistantStreamEvent[] = [
  { event: "tool_start", data: { id: "find-1", tool: "find_agents", kind: "search", title: "Searched the agent directory", target: "visa" } },
  {
    event: "a2a_discover",
    data: {
      query: "visa, resume and contract help",
      agents: [
        { slug: "visa-advisor", name: "Visa Advisor", tagline: "Work visas", price_cents: 200, card_url: "c1" },
        { slug: "resume-reviewer", name: "Resume Reviewer", tagline: "Resumes", price_cents: 200, card_url: "c2" },
        { slug: "contract-reviewer", name: "Contract Reviewer", tagline: "Contracts", price_cents: 600, card_url: "c3" },
      ],
    },
  },
  { event: "tool_end", data: { id: "find-1", tool: "find_agents", status: "completed" } },
];

function paidCall(slug: string, name: string, callId: string, spentAfter: number): AssistantStreamEvent[] {
  return [
    { event: "tool_start", data: { id: `call-${slug}`, tool: "call_agent", kind: "a2a", title: `Called ${name}`, target: slug } },
    {
      event: "a2a_call",
      data: { call_id: callId, slug, agent_name: name, endpoint: `https://api/a2a/${slug}`, price_cents: 200, request_preview: "Help" },
    },
    result({
      call_id: callId,
      slug,
      status: "completed",
      charged_cents: 200,
      creator_share_cents: 160,
      answer_preview: `${name} says hi`,
      mandate_spent_cents: spentAfter,
    }),
    { event: "tool_end", data: { id: `call-${slug}`, tool: "call_agent", status: "completed" } },
  ];
}

const rejectedContract: AssistantStreamEvent[] = [
  {
    event: "tool_start",
    data: { id: "call-contract", tool: "call_agent", kind: "a2a", title: "Called Contract Reviewer", target: "contract-reviewer" },
  },
  {
    event: "a2a_call",
    data: {
      call_id: null,
      slug: "contract-reviewer",
      agent_name: "Contract Reviewer",
      endpoint: "https://api/a2a/contract-reviewer",
      price_cents: 600,
      request_preview: "Check the offer letter",
    },
  },
  result({ slug: "contract-reviewer", status: "rejected", error_code: "mandate_exceeds_max", mandate_spent_cents: 400 }),
  { event: "tool_end", data: { id: "call-contract", tool: "call_agent", status: "error" } },
];

describe("assistant timeline reducer", () => {
  it("replays the demo storyline: discovery, two paid calls, one rejected call, final answer", () => {
    let s = start();
    s = feed(s, [
      status,
      ...discover,
      ...paidCall("visa-advisor", "Visa Advisor", "c-visa", 200),
      ...paidCall("resume-reviewer", "Resume Reviewer", "c-resume", 400),
      ...rejectedContract,
      { event: "text_delta", data: { delta: "Per Visa Advisor: " } },
      { event: "text_delta", data: { delta: "apply early." } },
      done("ok", { spent: 400, calls: 2 }),
    ]);

    const turn = turnOf(s);
    expect(turn.status).toBe("ok");
    expect(turn.model).toBe("gemini-3.8-flash");
    expect(turn.answer).toBe("Per Visa Advisor: apply early.");
    expect(turn.items.map((i) => i.kind)).toEqual(["discover", "call", "call", "call"]);

    const disc = turn.items[0] as DiscoverItem;
    expect(disc.status).toBe("done");
    expect(disc.agents.map((a) => a.slug)).toEqual(["visa-advisor", "resume-reviewer", "contract-reviewer"]);

    const [visa, resume, contract] = calls(s);
    expect(visa).toMatchObject({ slug: "visa-advisor", status: "completed", chargedCents: 200, creatorShareCents: 160, callId: "c-visa" });
    expect(visa.answerPreview).toBe("Visa Advisor says hi");
    expect(resume).toMatchObject({ status: "completed", chargedCents: 200 });
    expect(contract).toMatchObject({
      slug: "contract-reviewer",
      agentName: "Contract Reviewer",
      status: "rejected",
      errorCode: "mandate_exceeds_max",
      chargedCents: 0,
      priceCents: 600,
      callId: null,
    });

    expect(s.counters).toEqual({ id: "mdt_1", spentCents: 400, budgetCents: 2000, calls: 2, payerBalanceCents: 9600 });
    expect(summarizeTurn(turn)).toEqual({
      discovered: 3,
      calls: 3,
      completed: 2,
      rejected: 1,
      failed: 0,
      spentCents: 400,
      creatorShareCents: 320,
    });
  });

  it("does not move the mandate counters on a rejected call", () => {
    let s = start();
    s = feed(s, [status, ...paidCall("visa-advisor", "Visa Advisor", "c1", 200), ...paidCall("resume-reviewer", "Resume Reviewer", "c2", 400)]);
    const before = s.counters;
    s = feed(s, rejectedContract);
    expect(s.counters?.spentCents).toBe(before?.spentCents);
    expect(s.counters?.calls).toBe(before?.calls);
  });

  it("marks a call while it runs and keeps the agent name from the tool title", () => {
    let s = start();
    s = feed(s, [
      status,
      { event: "tool_start", data: { id: "x", tool: "call_agent", kind: "a2a", title: "Called Visa Advisor", target: "Visa-Advisor" } },
    ]);
    expect(calls(s)[0]).toMatchObject({ status: "pending", slug: "visa-advisor", agentName: "Visa Advisor", sent: false });
    s = feed(s, [
      {
        event: "a2a_call",
        data: { call_id: "abc", slug: "visa-advisor", agent_name: "Visa Advisor", endpoint: "e", price_cents: 200, request_preview: "r" },
      },
    ]);
    expect(calls(s)[0]).toMatchObject({ status: "running", sent: true, callId: "abc", priceCents: 200 });
  });

  it("closes a call-limit rejection that has no a2a_call frame", () => {
    let s = start();
    s = feed(s, [
      status,
      { event: "tool_start", data: { id: "c4", tool: "call_agent", kind: "a2a", title: "Called Visa Advisor", target: "visa-advisor" } },
      result({ slug: "visa-advisor", status: "rejected", error_code: "call_limit" }),
      { event: "tool_end", data: { id: "c4", tool: "call_agent", status: "error" } },
    ]);
    expect(calls(s)).toHaveLength(1);
    expect(calls(s)[0]).toMatchObject({ status: "rejected", errorCode: "call_limit", sent: false, chargedCents: 0 });
  });

  it("matches results by call id when two calls to the same agent overlap", () => {
    let s = start();
    s = feed(s, [
      status,
      { event: "tool_start", data: { id: "a", tool: "call_agent", kind: "a2a", title: "Called V", target: "v" } },
      { event: "a2a_call", data: { call_id: "id-a", slug: "v", agent_name: "V", endpoint: "e", price_cents: 100, request_preview: "" } },
      { event: "tool_start", data: { id: "b", tool: "call_agent", kind: "a2a", title: "Called V", target: "v" } },
      { event: "a2a_call", data: { call_id: "id-b", slug: "v", agent_name: "V", endpoint: "e", price_cents: 100, request_preview: "" } },
      result({ call_id: "id-b", slug: "v", status: "failed", error_code: "agent_run_failed" }),
      result({ call_id: "id-a", slug: "v", status: "completed", charged_cents: 100 }),
    ]);
    const [a, b] = calls(s);
    expect(a).toMatchObject({ callId: "id-a", status: "completed", chargedCents: 100 });
    expect(b).toMatchObject({ callId: "id-b", status: "failed", errorCode: "agent_run_failed", chargedCents: 0 });
  });

  it("creates items for frames that arrive without a tool_start", () => {
    let s = start();
    s = feed(s, [
      status,
      discover[1],
      { event: "a2a_call", data: { call_id: "z", slug: "visa-advisor", agent_name: "Visa Advisor", endpoint: "e", price_cents: 200, request_preview: "" } },
      result({ call_id: "z", slug: "visa-advisor", status: "completed", charged_cents: 200, mandate_spent_cents: 200 }),
    ]);
    expect(turnOf(s).items.map((i) => i.kind)).toEqual(["discover", "call"]);
    expect(calls(s)[0]).toMatchObject({ status: "completed", agentName: "Visa Advisor" });
  });

  it("records a mid-stream error and keeps it after done", () => {
    let s = start();
    s = feed(s, [
      status,
      { event: "text_delta", data: { delta: "Partial" } },
      { event: "error", data: { code: "turn_timeout", message: "The assistant took too long.", trace_id: "tr1" } },
      done("ok", { spent: 0, calls: 0 }),
    ]);
    expect(turnOf(s).error).toEqual({ code: "turn_timeout", message: "The assistant took too long.", traceId: "tr1" });
    expect(turnOf(s).answer).toBe("Partial");
  });

  it("handles a synthesized connection_lost done without a mandate field", () => {
    let s = start();
    s = feed(s, [status, ...paidCall("visa-advisor", "Visa Advisor", "c1", 200)]);
    s = feed(s, [
      { event: "tool_start", data: { id: "r", tool: "call_agent", kind: "a2a", title: "Called Resume Reviewer", target: "resume-reviewer" } },
      { event: "a2a_call", data: { call_id: "c2", slug: "resume-reviewer", agent_name: "Resume Reviewer", endpoint: "e", price_cents: 200, request_preview: "" } },
      { event: "error", data: { code: "connection_lost", message: "Connection lost", trace_id: "" } },
      done("aborted"),
    ]);
    const turn = turnOf(s);
    expect(turn.status).toBe("aborted");
    expect(turn.error?.code).toBe("connection_lost");
    // The open call is never assumed paid.
    expect(calls(s)[1]).toMatchObject({ status: "unknown", chargedCents: 0 });
    // Counters keep the last value reported by a2a_result.
    expect(s.counters).toMatchObject({ spentCents: 200, calls: 1 });
  });

  it("ignores frames after done and frames for unknown turns", () => {
    let s = start();
    s = feed(s, [status, { event: "text_delta", data: { delta: "A" } }, done("ok", { spent: 0, calls: 0 })]);
    const frozen = s;
    s = feed(s, [{ event: "text_delta", data: { delta: "B" } }]);
    expect(s).toBe(frozen);
    s = feed(s, [{ event: "text_delta", data: { delta: "C" } }], "other-turn");
    expect(s).toBe(frozen);
  });

  it("closes open items when the user stops a turn", () => {
    let s = start();
    s = feed(s, [
      status,
      discover[0],
      { event: "tool_start", data: { id: "c", tool: "call_agent", kind: "a2a", title: "Called V", target: "v" } },
    ]);
    s = assistantReducer(s, { type: "turn_stopped", turnId: T, now: 99 });
    const turn = turnOf(s);
    expect(turn.status).toBe("aborted");
    expect(turn.items[0]).toMatchObject({ kind: "discover", status: "error" });
    expect(turn.items[1]).toMatchObject({ kind: "call", status: "unknown" });
    // A later frame from the aborted stream changes nothing.
    expect(feed(s, [done("ok", { spent: 999, calls: 9 })])).toBe(s);
  });

  it("marks a turn rejected before the stream opened", () => {
    let s = start();
    s = assistantReducer(s, {
      type: "turn_failed",
      turnId: T,
      error: { code: "mandate_expired", message: "Expired", traceId: "t" },
      now: 5,
    });
    expect(turnOf(s)).toMatchObject({ status: "failed", rejectedBeforeStream: true, error: { code: "mandate_expired" } });
  });

  it("closes a discovery whose tool ended with an error", () => {
    let s = start();
    s = feed(s, [status, discover[0], { event: "tool_end", data: { id: "find-1", tool: "find_agents", status: "error" } }]);
    expect(turnOf(s).items[0]).toMatchObject({ kind: "discover", status: "error" });
  });

  it("keeps other tool lines as generic items", () => {
    let s = start();
    s = feed(s, [
      status,
      { event: "tool_start", data: { id: "w", tool: "google_search", kind: "web", title: "Searched the web", target: "q" } },
      { event: "tool_end", data: { id: "w", tool: "google_search", status: "completed" } },
    ]);
    expect(turnOf(s).items[0]).toMatchObject({ kind: "tool", title: "Searched the web", status: "completed" });
  });
});

describe("mandate counters", () => {
  it("is a no-op without a mandate", () => {
    expect(applyCounterEvent(null, result({ slug: "v", status: "completed", mandate_spent_cents: 5 }))).toBeNull();
  });

  it("takes done.mandate as the authoritative value for the same mandate only", () => {
    const next = applyCounterEvent(MANDATE, done("ok", { spent: 600, calls: 3 }));
    expect(next).toMatchObject({ spentCents: 600, calls: 3 });
    const other = applyCounterEvent({ ...MANDATE, id: "mdt_other" }, done("ok", { spent: 600, calls: 3 }));
    expect(other).toMatchObject({ spentCents: 0, calls: 0 });
  });

  it("clears and resets", () => {
    let s = start();
    s = assistantReducer(s, { type: "mandate_cleared" });
    expect(s.counters).toBeNull();
    s = assistantReducer(s, { type: "reset_turns" });
    expect(s.turns).toEqual([]);
  });
});

describe("buildHistory", () => {
  const turn = (userText: string, answer: string, st: Turn["status"] = "ok"): Turn => ({
    id: userText,
    userText,
    items: [],
    answer,
    status: st,
    model: null,
    error: null,
    rejectedBeforeStream: false,
    startedAt: 0,
    finishedAt: 1,
  });

  it("sends completed turns as pairs and ends with the new user message", () => {
    expect(buildHistory([turn("q1", "a1"), turn("q2", "", "failed")], "q3")).toEqual([
      { role: "user", text: "q1" },
      { role: "model", text: "a1" },
      { role: "user", text: "q3" },
    ]);
  });

  it("caps the history at 12 messages starting with a user message and clips long texts", () => {
    const turns = Array.from({ length: 10 }, (_, i) => turn(`q${i}`, `a${i}`));
    const out = buildHistory(turns, "x".repeat(5000));
    expect(out.length).toBeLessThanOrEqual(12);
    expect(out[0].role).toBe("user");
    expect(out[out.length - 1]).toEqual({ role: "user", text: "x".repeat(4000) });
  });
});
