import { describe, expect, it } from "vitest";
import type { VoiceServerEvent } from "./protocol";
import {
  closedMessage,
  formatClock,
  initialVoiceState,
  isActive,
  voiceActivity,
  voiceReducer,
  type VoiceAction,
  type VoiceState,
} from "./voiceState";

const ready: VoiceServerEvent = {
  type: "ready",
  session_id: "vs_1",
  conversation_id: "cnv_1",
  model: "gemini-live-2.5-flash",
  input_sample_rate: 16000,
  output_sample_rate: 24000,
  max_session_s: 600,
  idle_timeout_s: 90,
  next_round_charge_cents: 0,
  balance_cents: 1000,
};

const ACTIONS = new Set(["connect", "playing", "muted", "ending", "fail", "reset"]);

function run(...items: (VoiceServerEvent | VoiceAction)[]): VoiceState {
  return items.reduce<VoiceState>(
    (s, item) =>
      voiceReducer(
        s,
        ACTIONS.has(item.type) ? (item as VoiceAction) : { type: "server", event: item as VoiceServerEvent, now: 1000 },
      ),
    initialVoiceState,
  );
}

describe("voiceReducer", () => {
  it("goes connecting → live and tracks captions, tools and rounds", () => {
    let s = run({ type: "connect" }, ready);
    expect(s.phase).toBe("live");
    expect(s.conversationId).toBe("cnv_1");
    expect(s.startedAt).toBe(1000);
    expect(isActive(s)).toBe(true);
    expect(voiceActivity(s)).toBe("listening");

    s = voiceReducer(s, { type: "server", now: 1, event: { type: "transcript", turn_id: "v1", role: "user", text: "What is" } });
    s = voiceReducer(s, { type: "server", now: 1, event: { type: "transcript", turn_id: "v1", role: "user", text: " the fee?" } });
    expect(s.turns[0].user).toBe("What is the fee?");
    expect(voiceActivity(s)).toBe("thinking");

    const tool = { id: "c1", tool: "search_knowledge", kind: "search" as const, title: "Searched", target: "fee", status: "running" as const };
    s = voiceReducer(s, { type: "server", now: 1, event: { type: "tool", turn_id: "v1", tool } });
    s = voiceReducer(s, { type: "server", now: 1, event: { type: "tool", turn_id: "v1", tool: { ...tool, status: "completed" } } });
    expect(s.turns[0].tools).toEqual([{ ...tool, status: "completed" }]);

    s = voiceReducer(s, { type: "playing", playing: true });
    expect(voiceActivity(s)).toBe("speaking");
    s = voiceReducer(s, { type: "server", now: 1, event: { type: "transcript", turn_id: "v1", role: "model", text: "290 USD." } });
    s = voiceReducer(s, {
      type: "server",
      now: 1,
      event: {
        type: "round",
        turn_id: "v1",
        conversation_id: "cnv_1",
        message_id: "m_voice_1",
        outcome: "ok",
        citations: [],
        billing: { charged_cents: 100, free: false, round_index: 3, free_rounds_left: 0, balance_cents: 900 },
      },
    });
    expect(s.rounds).toBe(1);
    expect(s.balanceCents).toBe(900);
    expect(s.turns[0].outcome).toBe("ok");
    s = voiceReducer(s, { type: "playing", playing: false });
    expect(voiceActivity(s)).toBe("listening");
  });

  it("marks barge-in and stops speaking", () => {
    let s = run({ type: "connect" }, ready, { type: "playing", playing: true });
    s = voiceReducer(s, { type: "server", now: 1, event: { type: "transcript", turn_id: "v1", role: "model", text: "Once" } });
    s = voiceReducer(s, { type: "server", now: 1, event: { type: "interrupted", turn_id: "v1" } });
    expect(s.playing).toBe(false);
    expect(s.turns[0].interrupted).toBe(true);
    // An interruption for an unknown turn adds nothing.
    s = voiceReducer(s, { type: "server", now: 1, event: { type: "interrupted", turn_id: "v9" } });
    expect(s.turns).toHaveLength(1);
  });

  it("keeps the error phase through the closing event", () => {
    let s = run({ type: "connect" }, ready);
    s = voiceReducer(s, {
      type: "server",
      now: 1,
      event: { type: "error", code: "visitor_insufficient_points", message: "Top up", trace_id: "t1", detail: null },
    });
    s = voiceReducer(s, { type: "server", now: 1, event: { type: "closed", reason: "error", conversation_id: "cnv_1", rounds: 1, duration_s: 9 } });
    expect(s.phase).toBe("error");
    expect(s.error).toEqual({ code: "visitor_insufficient_points", message: "Top up", traceId: "t1", detail: null });
    expect(s.closedReason).toBe("error");
    expect(isActive(s)).toBe(false);
  });

  it("ends, fails only once, mutes and resets", () => {
    let s = run({ type: "connect" }, ready, { type: "muted", muted: true }, { type: "ending" });
    expect(s.muted).toBe(true);
    expect(s.phase).toBe("ending");
    s = voiceReducer(s, { type: "server", now: 1, event: { type: "closed", reason: "idle", conversation_id: null, rounds: 0, duration_s: 90 } });
    expect(s.phase).toBe("ended");
    s = voiceReducer(s, { type: "fail", error: { code: "x", message: "m", traceId: "", detail: null } });
    expect(s.phase).toBe("error");
    expect(voiceReducer(s, { type: "fail", error: { code: "y", message: "n", traceId: "", detail: null } }).error?.code).toBe("x");
    expect(voiceReducer(s, { type: "reset" })).toEqual(initialVoiceState);
    expect(voiceReducer(initialVoiceState, { type: "ending" }).phase).toBe("idle");
  });
});

describe("helpers", () => {
  it("formats the clock and closing reasons", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(187.9)).toBe("3:07");
    expect(formatClock(-5)).toBe("0:00");
    expect(closedMessage("max_duration")).toMatch(/time limit/);
    expect(closedMessage("idle")).toMatch(/quiet/);
    expect(closedMessage("replaced")).toMatch(/another tab/);
    expect(closedMessage("client")).toBeNull();
  });
});
