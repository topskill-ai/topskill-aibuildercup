/**
 * Pure chat transcript state: applies `ChatStreamEvent`s (SPEC §7.2) to a list of messages.
 * Kept free of React so the streaming rules are unit-tested directly.
 */
import { formatCredits } from "@/lib/money";
import type { ChatMessage, ChatStreamEvent, Citation, StreamErrorData, ToolLine } from "@/api/types";

export interface ViewCharge {
  charged_cents: number;
  free: boolean;
  /** null for the owner's own rounds (not numbered). */
  round_index: number | null;
}

export interface ViewMessage {
  id: string;
  role: "user" | "model";
  text: string;
  status: "ok" | "failed" | "aborted";
  turn_id: string;
  citations: Citation[];
  search_entry_point_html: string | null;
  tools: ToolLine[];
  charge: ViewCharge | null;
  created_at: string;
  /** Live-only fields (never sent by the server on restore). */
  streaming?: boolean;
  error?: StreamErrorData | null;
  balance_cents?: number | null;
  free_rounds_left?: number | null;
}

export interface ChatState {
  messages: ViewMessage[];
  /** Turn currently streaming (or waiting for the stream to open). */
  activeTurnId: string | null;
}

export type ChatAction =
  | { type: "reset"; messages: ChatMessage[] }
  | { type: "start"; turnId: string; text: string; now: string }
  | { type: "event"; turnId: string; event: ChatStreamEvent }
  /** The request failed before streaming: drop the optimistic pair. */
  | { type: "cancel"; turnId: string }
  /** The visitor stopped the turn and the stream ended without `done`. */
  | { type: "stopped"; turnId: string };

export const initialChatState: ChatState = { messages: [], activeTurnId: null };

export const pendingUserId = (turnId: string) => `pending-u-${turnId}`;
export const pendingModelId = (turnId: string) => `pending-m-${turnId}`;

export function fromWire(m: ChatMessage): ViewMessage {
  return {
    id: m.id,
    role: m.role,
    text: m.text,
    status: m.status,
    turn_id: m.turn_id,
    citations: m.citations ?? [],
    search_entry_point_html: m.search_entry_point_html ?? null,
    tools: m.tools ?? [],
    charge: m.charge ? { ...m.charge } : null,
    created_at: m.created_at,
  };
}

/** Back to the wire shape for the session transcript stash. */
export function toWire(m: ViewMessage): ChatMessage {
  return {
    id: m.id,
    role: m.role,
    text: m.text,
    status: m.status,
    turn_id: m.turn_id,
    citations: m.citations,
    search_entry_point_html: m.search_entry_point_html,
    tools: m.tools,
    charge: m.charge ? { charged_cents: m.charge.charged_cents, free: m.charge.free, round_index: m.charge.round_index ?? 0 } : null,
    created_at: m.created_at,
  };
}

function updateModel(state: ChatState, turnId: string, fn: (m: ViewMessage) => ViewMessage): ChatState {
  const id = pendingModelId(turnId);
  let changed = false;
  const messages = state.messages.map((m) => {
    if (m.id !== id && !(m.role === "model" && m.turn_id === turnId && m.streaming)) return m;
    changed = true;
    return fn(m);
  });
  return changed ? { ...state, messages } : state;
}

function closeRunningTools(tools: ToolLine[], status: ToolLine["status"]): ToolLine[] {
  return tools.some((t) => t.status === "running")
    ? tools.map((t) => (t.status === "running" ? { ...t, status } : t))
    : tools;
}

function applyEvent(state: ChatState, turnId: string, event: ChatStreamEvent): ChatState {
  switch (event.event) {
    case "status":
      return state;
    case "text_delta":
      if (!event.data.delta) return state;
      return updateModel(state, turnId, (m) => ({ ...m, text: m.text + event.data.delta }));
    case "tool_start": {
      const d = event.data;
      return updateModel(state, turnId, (m) => {
        const line: ToolLine = { id: d.id, tool: d.tool, kind: d.kind, title: d.title, target: d.target, status: "running" };
        const exists = m.tools.some((t) => t.id === d.id);
        return { ...m, tools: exists ? m.tools.map((t) => (t.id === d.id ? line : t)) : [...m.tools, line] };
      });
    }
    case "tool_end": {
      const d = event.data;
      return updateModel(state, turnId, (m) => ({
        ...m,
        tools: m.tools.map((t) => (t.id === d.id ? { ...t, status: d.status } : t)),
      }));
    }
    case "citations":
      return updateModel(state, turnId, (m) => ({
        ...m,
        citations: event.data.sources ?? [],
        search_entry_point_html: event.data.search_entry_point_html ?? null,
      }));
    case "error":
      return updateModel(state, turnId, (m) => ({ ...m, error: event.data }));
    case "done": {
      const d = event.data;
      const next = updateModel(state, turnId, (m) => ({
        ...m,
        id: d.message_id ?? m.id,
        status: d.outcome,
        streaming: false,
        tools: closeRunningTools(m.tools, d.outcome === "aborted" ? "interrupted" : "error"),
        charge: d.billing
          ? { charged_cents: d.billing.charged_cents, free: d.billing.free, round_index: d.billing.round_index }
          : null,
        balance_cents: d.billing ? d.billing.balance_cents : null,
        free_rounds_left: d.billing ? d.billing.free_rounds_left : null,
      }));
      const userId = pendingUserId(turnId);
      const messages = next.messages.map((m) =>
        m.id === userId ? { ...m, id: d.message_id ? `u_${d.message_id.replace(/^m_/, "")}` : m.id } : m,
      );
      return { messages, activeTurnId: state.activeTurnId === turnId ? null : state.activeTurnId };
    }
    default:
      return state; // unknown event names are ignored (SPEC §7.1)
  }
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "reset":
      return { messages: action.messages.map(fromWire), activeTurnId: null };
    case "start": {
      const user: ViewMessage = {
        id: pendingUserId(action.turnId),
        role: "user",
        text: action.text,
        status: "ok",
        turn_id: action.turnId,
        citations: [],
        search_entry_point_html: null,
        tools: [],
        charge: null,
        created_at: action.now,
      };
      const model: ViewMessage = {
        ...user,
        id: pendingModelId(action.turnId),
        role: "model",
        text: "",
        streaming: true,
        error: null,
      };
      return { messages: [...state.messages, user, model], activeTurnId: action.turnId };
    }
    case "event":
      return applyEvent(state, action.turnId, action.event);
    case "cancel":
      return {
        messages: state.messages.filter((m) => m.turn_id !== action.turnId || !m.id.startsWith("pending-")),
        activeTurnId: state.activeTurnId === action.turnId ? null : state.activeTurnId,
      };
    case "stopped": {
      const next = updateModel(state, action.turnId, (m) =>
        m.streaming
          ? { ...m, status: "aborted", streaming: false, tools: closeRunningTools(m.tools, "interrupted") }
          : m,
      );
      return { ...next, activeTurnId: state.activeTurnId === action.turnId ? null : state.activeTurnId };
    }
  }
}

export interface ChargeContext {
  pricingMode: "free" | "per_round";
  freeRounds: number;
}

/**
 * Per-message billing footer (SPEC §11.4): "Charged 1.00 · round 3", "Free round 1 of 2",
 * "Not charged (stopped)". Returns null while streaming or when nothing is known.
 */
export function chargeLabel(m: ViewMessage, ctx: ChargeContext): string | null {
  if (m.role !== "model" || m.streaming) return null;
  if (m.status === "aborted") {
    return m.error?.code === "connection_lost" ? "Connection lost" : "Not charged (stopped)";
  }
  if (m.status === "failed") return "Not charged";
  const c = m.charge;
  if (!c) return null;
  if (c.charged_cents > 0) {
    const round = c.round_index != null ? ` · round ${c.round_index}` : "";
    const balance = m.balance_cents != null ? ` · balance ${formatCredits(m.balance_cents)}` : "";
    return `Charged ${formatCredits(c.charged_cents)}${round}${balance}`;
  }
  if (c.round_index == null) return "Free for you (creator)";
  if (ctx.pricingMode === "free") return "Free";
  if (c.round_index <= ctx.freeRounds) return `Free round ${c.round_index} of ${ctx.freeRounds}`;
  return "Not charged";
}

