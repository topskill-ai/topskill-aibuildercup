/**
 * Enterprise Assistant timeline state (SPEC §7.5, §9.4, §11.4).
 *
 * The assistant stream interleaves tool lines, directory discovery, A2A calls and their results with
 * the final answer. This module folds those frames into a list of turns, each with an ordered list of
 * timeline items, plus the live counters of the mandate that pays for the calls. Everything here is
 * pure so it can be unit-tested without React.
 *
 * Matching rules (what the backend actually emits):
 * - `tool_start{kind:"search"}` opens a discovery item; `a2a_discover` fills it with agent cards.
 * - `tool_start{kind:"a2a", target: slug}` opens a pending call; `a2a_call` (same slug) attaches the
 *   call details; `a2a_result` closes it, matched by `call_id` when it has one, else by slug.
 * - A call rejected by the in-process pre-check has `call_id: null` on both frames. The per-turn call
 *   limit sends `a2a_result` without any `a2a_call`.
 * - `done` closes everything still open. A synthesized `connection_lost` done carries no `mandate`.
 */
import type {
  Mandate,
  A2ACallData,
  A2ADiscoverData,
  A2AResultData,
  AssistantDoneData,
  AssistantStreamEvent,
  DiscoveredAgent,
  ToolEndData,
  ToolStartData,
} from "@/api/types";

export type CallStatus = "pending" | "running" | "completed" | "failed" | "rejected" | "unknown";

export interface DiscoverItem {
  kind: "discover";
  key: string;
  toolId: string | null;
  query: string;
  status: "running" | "done" | "error";
  agents: DiscoveredAgent[];
}

export interface CallItem {
  kind: "call";
  key: string;
  toolId: string | null;
  slug: string;
  agentName: string;
  endpoint: string | null;
  /** Price quoted for this call (from `a2a_call`); null until known. */
  priceCents: number | null;
  requestPreview: string;
  callId: string | null;
  /** True once `a2a_call` arrived (the call passed or failed the pre-check). */
  sent: boolean;
  status: CallStatus;
  chargedCents: number;
  creatorShareCents: number;
  answerPreview: string;
  errorCode: string | null;
  startedAt: number;
  finishedAt: number | null;
}

export interface ToolItem {
  kind: "tool";
  key: string;
  toolId: string;
  tool: string;
  title: string;
  target: string;
  status: "running" | "completed" | "error" | "interrupted";
}

export type TimelineItem = DiscoverItem | CallItem | ToolItem;

export interface TurnError {
  code: string;
  message: string;
  traceId: string;
}

export type TurnStatus = "streaming" | "ok" | "failed" | "aborted";

export interface Turn {
  id: string;
  userText: string;
  items: TimelineItem[];
  answer: string;
  status: TurnStatus;
  model: string | null;
  error: TurnError | null;
  /** True when the turn never reached the server (pre-stream error such as an expired mandate). */
  rejectedBeforeStream: boolean;
  startedAt: number;
  finishedAt: number | null;
}

export interface MandateCounters {
  id: string;
  spentCents: number;
  budgetCents: number;
  calls: number;
  /** Payer wallet balance as last reported by the stream; null until the first result. */
  payerBalanceCents: number | null;
}

export interface AssistantState {
  turns: Turn[];
  counters: MandateCounters | null;
}

export type AssistantAction =
  | { type: "turn_started"; turnId: string; text: string; now: number }
  | { type: "stream_event"; turnId: string; event: AssistantStreamEvent; now: number }
  | { type: "turn_failed"; turnId: string; error: TurnError; now: number }
  | { type: "turn_stopped"; turnId: string; now: number }
  | { type: "mandate_set"; counters: MandateCounters }
  | { type: "mandate_cleared" }
  | { type: "reset_turns" };

export const initialAssistantState: AssistantState = { turns: [], counters: null };

// ---------------------------------------------------------------------------------------------
// Turn-level reducer

export function newTurn(id: string, userText: string, now: number): Turn {
  return {
    id,
    userText,
    items: [],
    answer: "",
    status: "streaming",
    model: null,
    error: null,
    rejectedBeforeStream: false,
    startedAt: now,
    finishedAt: null,
  };
}

function isOpenCall(item: TimelineItem): item is CallItem {
  return item.kind === "call" && (item.status === "pending" || item.status === "running");
}

function replaceAt(items: TimelineItem[], index: number, next: TimelineItem): TimelineItem[] {
  const copy = items.slice();
  copy[index] = next;
  return copy;
}

function agentNameFromTitle(title: string, fallback: string): string {
  const name = title.replace(/^Called\s+/i, "").trim();
  return name || fallback;
}

function onToolStart(turn: Turn, data: ToolStartData, now: number): Turn {
  const key = `tool:${data.id}`;
  if (turn.items.some((i) => i.key === key)) return turn;
  let item: TimelineItem;
  if (data.kind === "search" || data.tool === "find_agents") {
    item = { kind: "discover", key, toolId: data.id, query: data.target, status: "running", agents: [] };
  } else if (data.kind === "a2a" || data.tool === "call_agent") {
    const slug = data.target.trim().toLowerCase();
    item = {
      kind: "call",
      key,
      toolId: data.id,
      slug,
      agentName: agentNameFromTitle(data.title, slug),
      endpoint: null,
      priceCents: null,
      requestPreview: "",
      callId: null,
      sent: false,
      status: "pending",
      chargedCents: 0,
      creatorShareCents: 0,
      answerPreview: "",
      errorCode: null,
      startedAt: now,
      finishedAt: null,
    };
  } else {
    item = {
      kind: "tool",
      key,
      toolId: data.id,
      tool: data.tool,
      title: data.title,
      target: data.target,
      status: "running",
    };
  }
  return { ...turn, items: [...turn.items, item] };
}

function onDiscover(turn: Turn, data: A2ADiscoverData): Turn {
  const index = findLastIndex(turn.items, (i) => i.kind === "discover" && i.agents.length === 0);
  if (index >= 0) {
    const prev = turn.items[index] as DiscoverItem;
    return {
      ...turn,
      items: replaceAt(turn.items, index, { ...prev, query: data.query || prev.query, agents: data.agents }),
    };
  }
  const item: DiscoverItem = {
    kind: "discover",
    key: `discover:${turn.items.length}`,
    toolId: null,
    query: data.query,
    status: "done",
    agents: data.agents,
  };
  return { ...turn, items: [...turn.items, item] };
}

function emptyCall(key: string, slug: string, now: number): CallItem {
  return {
    kind: "call",
    key,
    toolId: null,
    slug,
    agentName: slug,
    endpoint: null,
    priceCents: null,
    requestPreview: "",
    callId: null,
    sent: false,
    status: "pending",
    chargedCents: 0,
    creatorShareCents: 0,
    answerPreview: "",
    errorCode: null,
    startedAt: now,
    finishedAt: null,
  };
}

function onCall(turn: Turn, data: A2ACallData, now: number): Turn {
  const slug = data.slug.trim().toLowerCase();
  let index = turn.items.findIndex((i) => i.kind === "call" && i.slug === slug && !i.sent && i.status === "pending");
  let items = turn.items;
  if (index < 0) {
    items = [...items, emptyCall(`call:${data.call_id ?? `${slug}:${items.length}`}`, slug, now)];
    index = items.length - 1;
  }
  const prev = items[index] as CallItem;
  const next: CallItem = {
    ...prev,
    slug,
    agentName: data.agent_name || prev.agentName,
    endpoint: data.endpoint,
    priceCents: data.price_cents,
    requestPreview: data.request_preview,
    callId: data.call_id,
    sent: true,
    status: "running",
  };
  return { ...turn, items: replaceAt(items, index, next) };
}

function onResult(turn: Turn, data: A2AResultData, now: number): Turn {
  const slug = data.slug.trim().toLowerCase();
  let index = -1;
  if (data.call_id) index = turn.items.findIndex((i) => i.kind === "call" && i.callId === data.call_id);
  if (index < 0) {
    index = turn.items.findIndex((i) => isOpenCall(i) && i.slug === slug && i.callId === null);
  }
  let items = turn.items;
  if (index < 0) {
    items = [...items, emptyCall(`result:${data.call_id ?? `${slug}:${items.length}`}`, slug, now)];
    index = items.length - 1;
  }
  const prev = items[index] as CallItem;
  const next: CallItem = {
    ...prev,
    callId: data.call_id ?? prev.callId,
    status: data.status,
    chargedCents: data.charged_cents,
    creatorShareCents: data.creator_share_cents,
    answerPreview: data.answer_preview,
    errorCode: data.error_code,
    finishedAt: now,
  };
  return { ...turn, items: replaceAt(items, index, next) };
}

function onToolEnd(turn: Turn, data: ToolEndData, now: number): Turn {
  const index = turn.items.findIndex((i) => i.toolId !== null && i.toolId === data.id);
  if (index < 0) return turn;
  const prev = turn.items[index];
  let next: TimelineItem = prev;
  if (prev.kind === "discover") {
    next = { ...prev, status: data.status === "completed" ? "done" : "error" };
  } else if (prev.kind === "tool") {
    next = { ...prev, status: data.status };
  } else if (isOpenCall(prev) && data.status === "error") {
    // The tool failed without reporting a result (e.g. an unexpected exception): never charged.
    next = { ...prev, status: "failed", errorCode: prev.errorCode ?? "agent_run_failed", finishedAt: now };
  }
  return next === prev ? turn : { ...turn, items: replaceAt(turn.items, index, next) };
}

/** Close everything still open when the turn ends. Open calls become `unknown` (never assumed paid). */
function closeOpenItems(items: TimelineItem[], now: number): TimelineItem[] {
  let changed = false;
  const next = items.map((item): TimelineItem => {
    if (item.kind === "discover" && item.status === "running") {
      changed = true;
      return { ...item, status: item.agents.length > 0 ? "done" : "error" };
    }
    if (item.kind === "tool" && item.status === "running") {
      changed = true;
      return { ...item, status: "interrupted" };
    }
    if (isOpenCall(item)) {
      changed = true;
      return { ...item, status: "unknown", finishedAt: now };
    }
    return item;
  });
  return changed ? next : items;
}

function onDone(turn: Turn, data: AssistantDoneData, now: number): Turn {
  return {
    ...turn,
    status: data.outcome,
    items: closeOpenItems(turn.items, now),
    finishedAt: now,
  };
}

/** Apply one stream frame to a turn. Frames after `done` are ignored. Unknown events are ignored. */
export function applyTurnEvent(turn: Turn, event: AssistantStreamEvent, now: number): Turn {
  if (turn.status !== "streaming") return turn;
  switch (event.event) {
    case "status":
      return { ...turn, model: event.data.model };
    case "text_delta":
      return event.data.delta ? { ...turn, answer: turn.answer + event.data.delta } : turn;
    case "tool_start":
      return onToolStart(turn, event.data, now);
    case "tool_end":
      return onToolEnd(turn, event.data, now);
    case "a2a_discover":
      return onDiscover(turn, event.data);
    case "a2a_call":
      return onCall(turn, event.data, now);
    case "a2a_result":
      return onResult(turn, event.data, now);
    case "error":
      return {
        ...turn,
        error: { code: event.data.code, message: event.data.message, traceId: event.data.trace_id },
      };
    case "done":
      return onDone(turn, event.data, now);
    default:
      return turn;
  }
}

// ---------------------------------------------------------------------------------------------
// Mandate counters

/** Update the live mandate counters from a stream frame (`a2a_result` and `done.mandate`). */
export function applyCounterEvent(counters: MandateCounters | null, event: AssistantStreamEvent): MandateCounters | null {
  if (!counters) return counters;
  if (event.event === "a2a_result") {
    const d = event.data;
    return {
      ...counters,
      spentCents: d.mandate_spent_cents,
      budgetCents: d.mandate_budget_cents || counters.budgetCents,
      calls: counters.calls + (d.status === "completed" ? 1 : 0),
      payerBalanceCents: d.payer_balance_cents,
    };
  }
  if (event.event === "done") {
    // A synthesized `connection_lost` done has no `mandate` field at all.
    const m = (event.data as Partial<AssistantDoneData>).mandate;
    if (!m || m.id !== counters.id) return counters;
    return { ...counters, spentCents: m.spent_cents, budgetCents: m.budget_cents, calls: m.calls };
  }
  return counters;
}

/** A mandate with its counters replaced by the live stream values (same mandate id only). */
export function liveMandate(m: Mandate, counters: MandateCounters | null): Mandate {
  if (!counters || counters.id !== m.id) return m;
  return { ...m, spent_cents: counters.spentCents, budget_cents: counters.budgetCents, calls: counters.calls };
}

// ---------------------------------------------------------------------------------------------
// Page reducer

function updateTurn(state: AssistantState, turnId: string, fn: (turn: Turn) => Turn): AssistantState {
  const index = state.turns.findIndex((t) => t.id === turnId);
  if (index < 0) return state;
  const prev = state.turns[index];
  const next = fn(prev);
  if (next === prev) return state;
  const turns = state.turns.slice();
  turns[index] = next;
  return { ...state, turns };
}

export function assistantReducer(state: AssistantState, action: AssistantAction): AssistantState {
  switch (action.type) {
    case "turn_started":
      return { ...state, turns: [...state.turns, newTurn(action.turnId, action.text, action.now)] };
    case "stream_event": {
      const target = state.turns.find((t) => t.id === action.turnId);
      if (!target || target.status !== "streaming") return state;
      const next = updateTurn(state, action.turnId, (t) => applyTurnEvent(t, action.event, action.now));
      const counters = applyCounterEvent(next.counters, action.event);
      return counters === next.counters ? next : { ...next, counters };
    }
    case "turn_failed":
      return updateTurn(state, action.turnId, (t) =>
        t.status !== "streaming"
          ? t
          : {
              ...t,
              status: "failed",
              error: action.error,
              rejectedBeforeStream: t.items.length === 0 && t.answer === "",
              items: closeOpenItems(t.items, action.now),
              finishedAt: action.now,
            },
      );
    case "turn_stopped":
      return updateTurn(state, action.turnId, (t) =>
        t.status !== "streaming"
          ? t
          : { ...t, status: "aborted", items: closeOpenItems(t.items, action.now), finishedAt: action.now },
      );
    case "mandate_set":
      return { ...state, counters: action.counters };
    case "mandate_cleared":
      return { ...state, counters: null };
    case "reset_turns":
      return { ...state, turns: [] };
    default:
      return state;
  }
}

// ---------------------------------------------------------------------------------------------
// Selectors

export interface TurnSummary {
  discovered: number;
  calls: number;
  completed: number;
  rejected: number;
  failed: number;
  spentCents: number;
  creatorShareCents: number;
}

export function summarizeTurn(turn: Turn): TurnSummary {
  const summary: TurnSummary = {
    discovered: 0,
    calls: 0,
    completed: 0,
    rejected: 0,
    failed: 0,
    spentCents: 0,
    creatorShareCents: 0,
  };
  const seen = new Set<string>();
  for (const item of turn.items) {
    if (item.kind === "discover") {
      for (const a of item.agents) seen.add(a.slug);
    } else if (item.kind === "call") {
      summary.calls += 1;
      if (item.status === "completed") summary.completed += 1;
      else if (item.status === "rejected") summary.rejected += 1;
      else if (item.status === "failed") summary.failed += 1;
      summary.spentCents += item.chargedCents;
      summary.creatorShareCents += item.creatorShareCents;
    }
  }
  summary.discovered = seen.size;
  return summary;
}

/**
 * Conversation history for `POST /api/demo/assistant/chat`: completed turns as user/model pairs,
 * then the new user message; at most `max` messages, each clipped to 4000 chars, last is the user.
 */
export function buildHistory(turns: Turn[], nextUserText: string, max = 12): { role: "user" | "model"; text: string }[] {
  const clip = (s: string) => (s.length > 4000 ? s.slice(0, 4000) : s);
  const pairs: { role: "user" | "model"; text: string }[] = [];
  for (const t of turns) {
    if (t.status !== "ok" || !t.answer.trim()) continue;
    pairs.push({ role: "user", text: clip(t.userText) }, { role: "model", text: clip(t.answer) });
  }
  pairs.push({ role: "user", text: clip(nextUserText) });
  let out = pairs.slice(-max);
  // Keep the history starting with a user message.
  if (out.length > 0 && out[0].role === "model") out = out.slice(1);
  return out;
}

function findLastIndex<T>(items: T[], pred: (item: T) => boolean): number {
  for (let i = items.length - 1; i >= 0; i -= 1) if (pred(items[i])) return i;
  return -1;
}

/**
 * For each timeline item, the agent slugs that earlier discover items of the same turn already
 * showed, so a repeated directory search only renders agents that are new.
 */
export function discoverSeenBefore(items: readonly TimelineItem[]): ReadonlySet<string>[] {
  const seen = new Set<string>();
  return items.map((item) => {
    const before = new Set(seen);
    if (item.kind === "discover") for (const a of item.agents) seen.add(a.slug);
    return before;
  });
}

/** A clipped preview ends in one ellipsis: "from Brazil.…" and "Pleas……" become "from Brazil…" / "Pleas…". */
export function tidyPreview(text: string): string {
  return text.replace(/[\s.,;:!?…]*(?:…|\.{3})$/, "…");
}
