import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const devUser = vi.hoisted(() => ({ value: null as null | { uid: string; role: "visitor" | "creator"; email?: string } }));
vi.mock("@/lib/devUser", () => ({ getDevUser: () => devUser.value }));
vi.mock("@/lib/firebase", () => ({ getIdToken: async () => "id-token-123" }));
const ensureSignedIn = vi.fn(async () => true);
vi.mock("@/components/LoginDialog", () => ({ useLoginDialog: () => ({ ensureSignedIn }) }));

/** Fake audio layer: records what the hook does with the mic and the player. */
const audio = vi.hoisted(() => {
  class MicError extends Error {
    readonly kind: string;
    constructor(kind: string, message: string) {
      super(message);
      this.kind = kind;
    }
  }
  const state = {
    startError: null as Error | null,
    onFrame: null as null | ((f: Int16Array) => void),
    micStopped: 0,
    played: 0,
    flushed: 0,
    ctxClosed: 0,
  };
  class MicCapture {
    muted = false;
    constructor(_ctx: unknown, onFrame: (f: Int16Array) => void) {
      state.onFrame = onFrame;
    }
    async start() {
      if (state.startError) throw state.startError;
    }
    stop() {
      state.micStopped += 1;
    }
  }
  class PcmPlayer {
    active = false;
    play() {
      state.played += 1;
    }
    flush() {
      state.flushed += 1;
    }
  }
  const createAudioContext = () => ({
    resume: async () => undefined,
    close: async () => {
      state.ctxClosed += 1;
    },
  });
  return { MicError, MicCapture, PcmPlayer, createAudioContext, state };
});
vi.mock("./audio", () => audio);

class FakeSocket {
  static last: FakeSocket | null = null;
  static OPEN = 1;
  readonly url: string;
  readyState = 0;
  binaryType = "blob";
  sent: (string | ArrayBuffer)[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string | ArrayBuffer }) => void) | null = null;
  onclose: (() => void) | null = null;
  closedWith: number | null = null;
  constructor(url: string) {
    this.url = url;
    FakeSocket.last = this;
  }
  send(data: string | ArrayBuffer) {
    this.sent.push(data);
  }
  close(code?: number) {
    this.closedWith = code ?? 1000;
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  server(event: object) {
    this.onmessage?.({ data: JSON.stringify(event) });
  }
  json(): Record<string, unknown>[] {
    return this.sent.filter((d): d is string => typeof d === "string").map((d) => JSON.parse(d));
  }
}

import { buildStartMessage, useVoice, voiceSocketUrl } from "./useVoice";

const READY = {
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

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function setup(conversationId: string | null = "cnv_1") {
  const cb = { onRound: vi.fn(), onEnded: vi.fn(), onPaywall: vi.fn(), onStaleConversation: vi.fn() };
  const hook = renderHook(() => useVoice({ slug: "visa-advisor", agentTitle: "Visa Advisor", conversationId, ...cb }), {
    wrapper,
  });
  return { hook, cb };
}

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  FakeSocket.last = null;
  Object.assign(audio.state, { startError: null, onFrame: null, micStopped: 0, played: 0, flushed: 0, ctxClosed: 0 });
  devUser.value = null;
  ensureSignedIn.mockResolvedValue(true);
});
afterEach(() => vi.unstubAllGlobals());

describe("voiceSocketUrl / buildStartMessage", () => {
  it("maps the API base to ws(s)", () => {
    expect(voiceSocketUrl("visa advisor", "https://api.example")).toBe("wss://api.example/api/public/agents/visa%20advisor/voice");
    expect(voiceSocketUrl("x", "http://127.0.0.1:8271")).toBe("ws://127.0.0.1:8271/api/public/agents/x/voice");
  });

  it("sends the ID token, or the developer identity in dev", async () => {
    expect(await buildStartMessage("cnv_1")).toEqual({ type: "start", conversation_id: "cnv_1", token: "id-token-123" });
    devUser.value = { uid: "dev_ana", role: "visitor" };
    expect(await buildStartMessage(null)).toEqual({ type: "start", conversation_id: null, dev: { uid: "dev_ana", role: "visitor" } });
  });
});

describe("useVoice", () => {
  it("runs a call: start frame, audio after ready, barge-in flush, rounds and the end", async () => {
    const { hook, cb } = setup();
    await act(() => hook.result.current.start());
    const ws = FakeSocket.last!;
    expect(ws.url).toMatch(/\/api\/public\/agents\/visa-advisor\/voice$/);
    expect(ws.binaryType).toBe("arraybuffer");
    expect(hook.result.current.state.phase).toBe("connecting");

    // Mic frames before `ready` are not sent.
    audio.state.onFrame!(new Int16Array(640));
    act(() => ws.open());
    await waitFor(() => expect(ws.json()).toEqual([{ type: "start", conversation_id: "cnv_1", token: "id-token-123" }]));
    act(() => ws.server(READY));
    expect(hook.result.current.state.phase).toBe("live");
    audio.state.onFrame!(new Int16Array(640));
    expect(ws.sent.filter((d) => typeof d !== "string")).toHaveLength(1);

    act(() => ws.onmessage!({ data: new ArrayBuffer(4800) }));
    expect(audio.state.played).toBe(1);
    act(() => ws.server({ type: "interrupted", turn_id: "v1" }));
    expect(audio.state.flushed).toBe(1);

    act(() =>
      ws.server({ type: "round", turn_id: "v1", conversation_id: "cnv_1", message_id: "m", outcome: "ok", citations: [], billing: null }),
    );
    expect(cb.onRound).toHaveBeenCalledWith("cnv_1");

    act(() => hook.result.current.setMuted(true));
    expect(hook.result.current.state.muted).toBe(true);

    act(() => hook.result.current.end());
    expect(ws.json().at(-1)).toEqual({ type: "stop" });
    expect(hook.result.current.state.phase).toBe("ending");
    act(() => ws.server({ type: "closed", reason: "client", conversation_id: "cnv_1", rounds: 1, duration_s: 12 }));
    expect(hook.result.current.state.phase).toBe("ended");
    expect(cb.onEnded).toHaveBeenCalledWith("cnv_1");
    expect(ws.closedWith).toBe(1000);
    expect(audio.state.micStopped).toBeGreaterThan(0);
  });

  it("explains a blocked microphone without opening a socket", async () => {
    audio.state.startError = new audio.MicError("denied", "Microphone access is blocked.");
    const { hook } = setup();
    await act(() => hook.result.current.start());
    expect(FakeSocket.last).toBeNull();
    expect(hook.result.current.state.phase).toBe("error");
    expect(hook.result.current.state.error?.code).toBe("mic_denied");
  });

  it("does nothing when sign-in is cancelled", async () => {
    ensureSignedIn.mockResolvedValue(false);
    const { hook } = setup();
    await act(() => hook.result.current.start());
    expect(FakeSocket.last).toBeNull();
    expect(hook.result.current.state.phase).toBe("idle");
  });

  it("opens the paywall on 402 and starts over on a foreign conversation", async () => {
    const { hook, cb } = setup();
    await act(() => hook.result.current.start());
    const ws = FakeSocket.last!;
    act(() => ws.open());
    act(() =>
      ws.server({ type: "error", code: "visitor_insufficient_points", message: "You need 1.00 credits", trace_id: "tr1", detail: {} }),
    );
    expect(cb.onPaywall).toHaveBeenCalled();
    expect(hook.result.current.state.error).toMatchObject({ code: "visitor_insufficient_points", traceId: "tr1" });

    const second = setup();
    await act(() => second.hook.result.current.start());
    act(() => FakeSocket.last!.open());
    act(() => FakeSocket.last!.server({ type: "error", code: "session_not_owned", message: "x", trace_id: "t", detail: null }));
    expect(second.cb.onStaleConversation).toHaveBeenCalled();
  });

  it("reports a dropped connection with connection_lost", async () => {
    const { hook, cb } = setup(null);
    await act(() => hook.result.current.start());
    const ws = FakeSocket.last!;
    act(() => ws.open());
    act(() => ws.server(READY));
    act(() => ws.onclose!());
    expect(hook.result.current.state.phase).toBe("error");
    expect(hook.result.current.state.error?.code).toBe("connection_lost");
    expect(cb.onEnded).toHaveBeenCalledWith(null);
    act(() => hook.result.current.reset());
    expect(hook.result.current.state.phase).toBe("idle");
  });
});
