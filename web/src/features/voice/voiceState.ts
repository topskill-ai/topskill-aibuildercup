/**
 * Pure state of one voice call (SPEC §18.1): connection phase, live captions per round, and the
 * activity shown to the visitor (listening / thinking / speaking).
 */
import type { ToolLine } from "@/api/types";
import type { ClosedReason, VoiceBilling, VoiceServerEvent } from "./protocol";

export type VoicePhase = "idle" | "connecting" | "live" | "ending" | "ended" | "error";
export type VoiceActivity = "listening" | "thinking" | "speaking";

export interface CaptionTurn {
  turnId: string;
  user: string;
  model: string;
  tools: ToolLine[];
  outcome: "ok" | "failed" | "aborted" | null;
  billing: VoiceBilling | null;
  interrupted: boolean;
}

export interface VoiceError {
  code: string;
  message: string;
  traceId: string;
  detail: Record<string, unknown> | null;
}

export interface VoiceState {
  phase: VoicePhase;
  conversationId: string | null;
  model: string | null;
  maxSessionS: number | null;
  startedAt: number | null;
  turns: CaptionTurn[];
  playing: boolean;
  muted: boolean;
  error: VoiceError | null;
  closedReason: ClosedReason | null;
  rounds: number;
  balanceCents: number | null;
}

export const initialVoiceState: VoiceState = {
  phase: "idle",
  conversationId: null,
  model: null,
  maxSessionS: null,
  startedAt: null,
  turns: [],
  playing: false,
  muted: false,
  error: null,
  closedReason: null,
  rounds: 0,
  balanceCents: null,
};

export type VoiceAction =
  | { type: "connect" }
  | { type: "server"; event: VoiceServerEvent; now: number }
  | { type: "playing"; playing: boolean }
  | { type: "muted"; muted: boolean }
  | { type: "ending" }
  | { type: "fail"; error: VoiceError }
  | { type: "reset" };

const MAX_TURNS = 20;

function withTurn(turns: CaptionTurn[], turnId: string, update: (t: CaptionTurn) => CaptionTurn): CaptionTurn[] {
  const i = turns.findIndex((t) => t.turnId === turnId);
  if (i >= 0) return turns.map((t, j) => (j === i ? update(t) : t));
  const fresh: CaptionTurn = { turnId, user: "", model: "", tools: [], outcome: null, billing: null, interrupted: false };
  return [...turns, update(fresh)].slice(-MAX_TURNS);
}

function onServer(state: VoiceState, event: VoiceServerEvent, now: number): VoiceState {
  switch (event.type) {
    case "ready":
      return {
        ...state,
        phase: state.phase === "connecting" ? "live" : state.phase,
        conversationId: event.conversation_id,
        model: event.model,
        maxSessionS: event.max_session_s,
        startedAt: now,
        balanceCents: event.balance_cents,
      };
    case "transcript":
      return {
        ...state,
        turns: withTurn(state.turns, event.turn_id, (t) =>
          event.role === "user" ? { ...t, user: t.user + event.text } : { ...t, model: t.model + event.text },
        ),
      };
    case "tool":
      return {
        ...state,
        turns: withTurn(state.turns, event.turn_id, (t) => ({
          ...t,
          tools: t.tools.some((x) => x.id === event.tool.id)
            ? t.tools.map((x) => (x.id === event.tool.id ? event.tool : x))
            : [...t.tools, event.tool],
        })),
      };
    case "interrupted":
      return {
        ...state,
        playing: false,
        turns: state.turns.some((t) => t.turnId === event.turn_id)
          ? withTurn(state.turns, event.turn_id, (t) => ({ ...t, interrupted: true }))
          : state.turns,
      };
    case "round":
      return {
        ...state,
        conversationId: event.conversation_id,
        rounds: state.rounds + (event.outcome === "ok" ? 1 : 0),
        balanceCents: event.billing?.balance_cents ?? state.balanceCents,
        turns: withTurn(state.turns, event.turn_id, (t) => ({ ...t, outcome: event.outcome, billing: event.billing })),
      };
    case "error":
      return {
        ...state,
        phase: "error",
        error: { code: event.code, message: event.message, traceId: event.trace_id, detail: event.detail },
      };
    case "closed":
      return {
        ...state,
        phase: state.phase === "error" ? "error" : "ended",
        closedReason: event.reason,
        conversationId: event.conversation_id ?? state.conversationId,
      };
    default:
      return state;
  }
}

export function voiceReducer(state: VoiceState, action: VoiceAction): VoiceState {
  switch (action.type) {
    case "connect":
      return { ...initialVoiceState, phase: "connecting" };
    case "server":
      return onServer(state, action.event, action.now);
    case "playing":
      return { ...state, playing: action.playing };
    case "muted":
      return { ...state, muted: action.muted };
    case "ending":
      return state.phase === "live" || state.phase === "connecting" ? { ...state, phase: "ending" } : state;
    case "fail":
      return state.phase === "error" ? state : { ...state, phase: "error", error: action.error, playing: false };
    case "reset":
      return initialVoiceState;
    default:
      return state;
  }
}

/** What the visitor should see the agent doing right now. */
export function voiceActivity(state: VoiceState): VoiceActivity {
  if (state.playing) return "speaking";
  const last = state.turns[state.turns.length - 1];
  if (last && last.outcome === null && !last.interrupted) {
    if (last.tools.some((t) => t.status === "running")) return "thinking";
    if (last.user.trim() && !last.model.trim()) return "thinking";
  }
  return "listening";
}

export function isActive(state: VoiceState): boolean {
  return state.phase === "connecting" || state.phase === "live" || state.phase === "ending";
}

/** "3:07" */
export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** One-line explanation of why a call ended (null for a call the visitor ended). */
export function closedMessage(reason: ClosedReason | null): string | null {
  switch (reason) {
    case "max_duration":
      return "The call reached its time limit.";
    case "idle":
      return "The call ended after a quiet stretch.";
    case "replaced":
      return "The call continued in another tab.";
    case "server":
      return "The call was ended by the server.";
    default:
      return null;
  }
}
