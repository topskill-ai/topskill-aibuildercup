/**
 * Wire types of the voice WebSocket `WS /api/public/agents/{slug}/voice` (SPEC §18.1).
 * Binary frames are raw PCM16 little-endian mono audio: 16 kHz from the browser, 24 kHz from the server.
 */
import type { Citation, ToolLine } from "@/api/types";

export interface VoiceConfig {
  enabled: boolean;
  max_session_s: number;
  idle_timeout_s: number;
  input_sample_rate: number;
  output_sample_rate: number;
}

export interface VoiceBilling {
  charged_cents: number;
  free: boolean;
  round_index: number | null;
  free_rounds_left: number | null;
  balance_cents: number;
}

export type ClosedReason = "client" | "max_duration" | "idle" | "replaced" | "error" | "server";

export type VoiceServerEvent =
  | {
      type: "ready";
      session_id: string;
      conversation_id: string;
      model: string;
      input_sample_rate: number;
      output_sample_rate: number;
      max_session_s: number;
      idle_timeout_s: number;
      next_round_charge_cents: number;
      balance_cents: number;
    }
  | { type: "transcript"; turn_id: string; role: "user" | "model"; text: string }
  | { type: "tool"; turn_id: string; tool: ToolLine }
  | { type: "interrupted"; turn_id: string }
  | {
      type: "round";
      turn_id: string;
      conversation_id: string;
      message_id: string;
      outcome: "ok" | "failed" | "aborted";
      citations: Citation[];
      billing: VoiceBilling | null;
    }
  | { type: "error"; code: string; message: string; trace_id: string; detail: Record<string, unknown> | null }
  | { type: "closed"; reason: ClosedReason; conversation_id: string | null; rounds: number; duration_s: number }
  | { type: "pong" };

export type VoiceStartMessage = {
  type: "start";
  conversation_id: string | null;
  token?: string;
  dev?: { uid: string; role?: string; email?: string };
};

/** Client-side error codes (never sent by the server). */
export const VoiceClientCodes = {
  mic_denied: "mic_denied",
  mic_unavailable: "mic_unavailable",
  insecure_context: "insecure_context",
  connection_lost: "connection_lost",
} as const;
