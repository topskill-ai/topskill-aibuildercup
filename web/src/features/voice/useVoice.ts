/**
 * Voice call controller for the agent page (SPEC §18.1).
 *
 * Order on "start": sign-in → microphone permission (fails fast, before any network) → WebSocket to the
 * Cloud Run URL (Firebase Hosting does not proxy WebSockets) → `start` frame with the Firebase ID token
 * (or the developer identity in dev) → audio frames only after `ready`.
 * Barge-in: the server's `interrupted` event flushes queued playback at once; as a local fallback, a
 * caption of the visitor's next utterance (another `turn_id` than the audio being played, announced by
 * `speaking`) flushes it too.
 * A connection that is not `ready` within CONNECT_TIMEOUT_MS fails with `connection_lost`.
 */
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef } from "react";
import { api } from "@/api/client";
import { useLoginDialog } from "@/components/LoginDialog";
import { config } from "@/lib/config";
import { getDevUser } from "@/lib/devUser";
import { getIdToken } from "@/lib/firebase";
import { MicCapture, MicError, PcmPlayer, createAudioContext } from "./audio";
import { pcm16ToFloat32, rmsLevel } from "./pcm";
import { VoiceClientCodes, type VoiceConfig, type VoiceServerEvent, type VoiceStartMessage } from "./protocol";
import { initialVoiceState, voiceReducer, type VoiceError } from "./voiceState";

const OUTPUT_RATE = 24_000;
const INPUT_RATE = 16_000;
const STOP_GRACE_MS = 3_000;
/** Above the server's worst case (two Live plans × VOICE_CONNECT_TIMEOUT_S 12 s). */
export const CONNECT_TIMEOUT_MS = 30_000;

/** `GET /api/public/voice/config`; any failure hides the mic button (text chat is unaffected). */
export function useVoiceConfig() {
  return useQuery({
    queryKey: ["voice", "config"],
    queryFn: ({ signal }) => api.get<VoiceConfig>("/api/public/voice/config", { signal }),
    staleTime: 5 * 60_000,
    retry: false,
  });
}

export function voiceSocketUrl(slug: string, apiBase: string = config.apiBase): string {
  const base = apiBase.replace(/^http(s?):\/\//i, (_m, s: string) => `ws${s}://`);
  return `${base}/api/public/agents/${encodeURIComponent(slug)}/voice`;
}

export async function buildStartMessage(conversationId: string | null): Promise<VoiceStartMessage> {
  const msg: VoiceStartMessage = { type: "start", conversation_id: conversationId };
  const dev = getDevUser();
  if (dev) {
    msg.dev = { uid: dev.uid, role: dev.role, ...(dev.email ? { email: dev.email } : {}) };
    return msg;
  }
  const token = await getIdToken(false).catch(() => null);
  if (token) msg.token = token;
  return msg;
}

function clientError(code: string, message: string): VoiceError {
  return { code, message, traceId: "", detail: null };
}

export interface UseVoiceOptions {
  slug: string;
  agentTitle: string;
  /** The text chat's current conversation: the call continues it (null = a new conversation). */
  conversationId: string | null;
  /** A round was persisted: refresh the transcript of `conversationId`. */
  onRound?: (conversationId: string) => void;
  /** The call ended; `conversationId` is null when nothing was saved. */
  onEnded?: (conversationId: string | null) => void;
  /** 402 `visitor_insufficient_points`: open the top-up dialog. */
  onPaywall?: () => void;
  /** 404 `session_not_owned`: the stored conversation belongs to someone else; start a new one. */
  onStaleConversation?: () => void;
}

interface Call {
  socket: WebSocket | null;
  ctx: AudioContext | null;
  mic: MicCapture | null;
  player: PcmPlayer | null;
  terminal: boolean;
  stopTimer: ReturnType<typeof setTimeout> | null;
  connectTimer: ReturnType<typeof setTimeout> | null;
  /** The round whose audio the player holds (from `speaking` / model captions). */
  playingTurnId: string | null;
}

export function useVoice(opts: UseVoiceOptions) {
  const { ensureSignedIn } = useLoginDialog();
  const [state, dispatch] = useReducer(voiceReducer, initialVoiceState);
  const callRef = useRef<Call | null>(null);
  const optsRef = useRef(opts);
  const levelRef = useRef<(level: number) => void>(() => undefined);
  useEffect(() => {
    optsRef.current = opts;
  });

  const teardown = useCallback((call: Call, { flush }: { flush: boolean }) => {
    call.mic?.stop();
    call.mic = null;
    if (call.stopTimer) clearTimeout(call.stopTimer);
    call.stopTimer = null;
    if (call.connectTimer) clearTimeout(call.connectTimer);
    call.connectTimer = null;
    const ctx = call.ctx;
    const player = call.player;
    if (flush) player?.flush();
    if (ctx && (!player || !player.active)) {
      void ctx.close().catch(() => undefined);
      call.ctx = null;
    }
    if (call.socket && call.socket.readyState <= WebSocket.OPEN) call.socket.close(1000);
    call.socket = null;
  }, []);

  const fail = useCallback(
    (call: Call, error: VoiceError) => {
      call.terminal = true;
      dispatch({ type: "fail", error });
      teardown(call, { flush: true });
    },
    [teardown],
  );

  const start = useCallback(async () => {
    if (callRef.current && !callRef.current.terminal) return;
    const signedIn = await ensureSignedIn({ reason: `Sign in to talk with ${optsRef.current.agentTitle}.` });
    if (!signedIn) return;

    const call: Call = {
      socket: null,
      ctx: null,
      mic: null,
      player: null,
      terminal: false,
      stopTimer: null,
      connectTimer: null,
      playingTurnId: null,
    };
    callRef.current = call;
    dispatch({ type: "connect" });

    try {
      call.ctx = createAudioContext();
      await call.ctx.resume().catch(() => undefined);
      const ctx = call.ctx;
      call.player = new PcmPlayer(ctx, OUTPUT_RATE, (playing) => {
        dispatch({ type: "playing", playing });
        // After the call ended, release the audio device once the last words were played.
        if (!playing && call.terminal && call.ctx && !call.mic) {
          void call.ctx.close().catch(() => undefined);
          call.ctx = null;
        }
      });
      let ready = false;
      call.mic = new MicCapture(ctx, (frame) => {
        levelRef.current(rmsLevel(frame));
        const socket = call.socket;
        if (ready && socket && socket.readyState === WebSocket.OPEN) socket.send(frame.buffer as ArrayBuffer);
      });
      await call.mic.start(INPUT_RATE);
      if (callRef.current !== call || call.terminal) {
        teardown(call, { flush: true });
        return;
      }

      const socket = new WebSocket(voiceSocketUrl(optsRef.current.slug));
      socket.binaryType = "arraybuffer";
      call.socket = socket;
      call.connectTimer = setTimeout(() => {
        call.connectTimer = null;
        if (ready || call.terminal) return;
        fail(call, clientError(VoiceClientCodes.connection_lost, "The voice connection could not be set up. Please try again."));
        optsRef.current.onEnded?.(null);
      }, CONNECT_TIMEOUT_MS);
      socket.onopen = () => {
        void buildStartMessage(optsRef.current.conversationId).then((msg) => {
          if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(msg));
        });
      };
      socket.onmessage = (e: MessageEvent<ArrayBuffer | string>) => {
        if (typeof e.data !== "string") {
          call.player?.play(pcm16ToFloat32(e.data));
          return;
        }
        let event: VoiceServerEvent;
        try {
          event = JSON.parse(e.data) as VoiceServerEvent;
        } catch {
          return;
        }
        const o = optsRef.current;
        switch (event.type) {
          case "ready":
            ready = true;
            if (call.connectTimer) clearTimeout(call.connectTimer);
            call.connectTimer = null;
            break;
          case "speaking":
            call.playingTurnId = event.turn_id;
            break;
          case "transcript":
            if (event.role === "model") {
              call.playingTurnId = event.turn_id;
            } else if (call.playingTurnId !== null && event.turn_id !== call.playingTurnId) {
              // The visitor speaks over an answer the server already finished generating: stop it here
              // even if no `interrupted` event arrives.
              call.player?.flush();
              call.playingTurnId = null;
            }
            break;
          case "interrupted":
            call.player?.flush();
            call.playingTurnId = null;
            break;
          case "round":
            o.onRound?.(event.conversation_id);
            break;
          case "error":
            call.terminal = true;
            if (event.code === "visitor_insufficient_points") o.onPaywall?.();
            if (event.code === "session_not_owned") o.onStaleConversation?.();
            break;
          case "closed":
            call.terminal = true;
            teardown(call, { flush: event.reason === "client" || event.reason === "replaced" });
            o.onEnded?.(event.conversation_id);
            break;
        }
        dispatch({ type: "server", event, now: Date.now() });
      };
      socket.onclose = () => {
        if (!call.terminal) {
          fail(call, clientError(VoiceClientCodes.connection_lost, "The voice connection was lost. Please try again."));
          optsRef.current.onEnded?.(null);
        } else {
          teardown(call, { flush: false });
        }
      };
    } catch (err) {
      if (err instanceof MicError) {
        const code =
          err.kind === "denied"
            ? VoiceClientCodes.mic_denied
            : err.kind === "insecure"
              ? VoiceClientCodes.insecure_context
              : VoiceClientCodes.mic_unavailable;
        fail(call, clientError(code, err.message));
      } else {
        fail(call, clientError(VoiceClientCodes.mic_unavailable, "Voice could not start in this browser."));
      }
    }
  }, [ensureSignedIn, fail, teardown]);

  /** Hang up: stop listening and playing now; the server settles what was answered. */
  const end = useCallback(() => {
    const call = callRef.current;
    if (!call || call.terminal) return;
    dispatch({ type: "ending" });
    call.mic?.stop();
    call.mic = null;
    call.player?.flush();
    const socket = call.socket;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "stop" }));
      call.stopTimer = setTimeout(() => {
        call.terminal = true;
        teardown(call, { flush: true });
        dispatch({ type: "server", event: { type: "closed", reason: "client", conversation_id: null, rounds: 0, duration_s: 0 }, now: Date.now() });
      }, STOP_GRACE_MS);
    } else {
      call.terminal = true;
      teardown(call, { flush: true });
      dispatch({ type: "reset" });
    }
  }, [teardown]);

  const setMuted = useCallback((muted: boolean) => {
    const call = callRef.current;
    if (call?.mic) call.mic.muted = muted;
    dispatch({ type: "muted", muted });
  }, []);

  const reset = useCallback(() => {
    const call = callRef.current;
    if (call && !call.terminal) return;
    callRef.current = null;
    dispatch({ type: "reset" });
  }, []);

  useEffect(
    () => () => {
      const call = callRef.current;
      if (!call) return;
      call.terminal = true;
      if (call.socket?.readyState === WebSocket.OPEN) call.socket.send(JSON.stringify({ type: "stop" }));
      teardown(call, { flush: true });
      if (call.ctx) void call.ctx.close().catch(() => undefined);
    },
    [teardown],
  );

  return {
    state,
    start,
    end,
    setMuted,
    reset,
    /** The panel registers a callback that receives the mic level (0..1) ~25 times a second. */
    onLevel: (fn: (level: number) => void) => {
      levelRef.current = fn;
    },
  };
}

export type VoiceController = ReturnType<typeof useVoice>;
