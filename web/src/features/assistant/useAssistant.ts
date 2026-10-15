/**
 * Enterprise Assistant session (SPEC §7.5, §9.4): the mandate token kept in page memory, the turn
 * timeline reducer, and `POST /api/demo/assistant/chat` streaming.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api } from "@/api/client";
import { ErrorCodes, errorMessage, errorTraceId, isApiError } from "@/api/errors";
import { queryKeys } from "@/api/queryClient";
import { streamSSE } from "@/api/sse";
import type { AssistantStreamEvent, Mandate, Page } from "@/api/types";
import type { CreateMandateResponse } from "@/features/mandates/api";
import { assistantReducer, buildHistory, initialAssistantState, type MandateCounters } from "./timeline";

export const ASSISTANT_CHAT_PATH = "/api/demo/assistant/chat";

/** Pre-stream error codes that make the current mandate unusable (ask for a new one). */
const MANDATE_DEAD_CODES = new Set<string>([
  ErrorCodes.mandate_expired,
  ErrorCodes.mandate_revoked,
  ErrorCodes.mandate_invalid,
  ErrorCodes.mandate_missing,
  ErrorCodes.mandate_budget_exhausted,
  ErrorCodes.forbidden,
]);

export interface ActiveMandate {
  mandate: Mandate;
  token: string;
  /** Set when the server rejected the token (expired, revoked, …) or the user revoked it here. */
  dead: string | null;
}

function countersOf(m: Mandate): MandateCounters {
  return { id: m.id, spentCents: m.spent_cents, budgetCents: m.budget_cents, calls: m.calls, payerBalanceCents: null };
}

function newTurnId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID().replace(/-/g, "");
  return `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

export function useAssistant() {
  const qc = useQueryClient();
  const [state, dispatch] = useReducer(assistantReducer, initialAssistantState);
  const [active, setActive] = useState<ActiveMandate | null>(null);
  const [streamingTurn, setStreamingTurn] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const refreshMoney = useCallback(() => {
    void qc.invalidateQueries({ queryKey: queryKeys.wallet });
    void qc.invalidateQueries({ queryKey: ["ledger"] });
    void qc.invalidateQueries({ queryKey: queryKeys.mandates });
    void qc.invalidateQueries({ queryKey: queryKeys.dashboard });
  }, [qc]);

  /** Re-read the mandate counters from the server (after a lost connection). */
  const syncMandate = useCallback(async (mandateId: string) => {
    try {
      const page = await api.get<Page<Mandate>>("/api/mandates", { query: { limit: 20 } });
      const m = page.items.find((x) => x.id === mandateId);
      if (!m) return;
      dispatch({ type: "mandate_set", counters: countersOf(m) });
      setActive((prev) => (prev && prev.mandate.id === m.id ? { ...prev, mandate: m } : prev));
    } catch {
      /* the [SHOULD] list endpoint may be missing; keep the last streamed counters */
    }
  }, []);

  const authorised = useCallback((res: CreateMandateResponse) => {
    setActive({ mandate: res.mandate, token: res.token, dead: null });
    dispatch({ type: "mandate_set", counters: countersOf(res.mandate) });
  }, []);

  const markRevoked = useCallback((m: Mandate) => {
    setActive((prev) => (prev && prev.mandate.id === m.id ? { ...prev, mandate: m, dead: ErrorCodes.mandate_revoked } : prev));
  }, []);

  const forgetMandate = useCallback(() => {
    setActive(null);
    dispatch({ type: "mandate_cleared" });
  }, []);

  const stop = useCallback(() => {
    const turnId = streamingTurn;
    abortRef.current?.abort();
    abortRef.current = null;
    if (turnId) dispatch({ type: "turn_stopped", turnId, now: Date.now() });
    setStreamingTurn(null);
    refreshMoney();
  }, [streamingTurn, refreshMoney]);

  /** Start a turn with an explicit token (the caller has checked that a mandate is usable). */
  const send = useCallback(
    async (text: string, token: string, mandateId: string) => {
      const message = text.trim();
      if (!message || abortRef.current) return;
      const history = buildHistory(stateRef.current.turns, message);
      const turnId = newTurnId();
      const controller = new AbortController();
      abortRef.current = controller;
      setStreamingTurn(turnId);
      dispatch({ type: "turn_started", turnId, text: message, now: Date.now() });

      let sawMandateCounters = false;
      try {
        await streamSSE<AssistantStreamEvent>(
          ASSISTANT_CHAT_PATH,
          { messages: history, mandate_token: token },
          {
            signal: controller.signal,
            onEvent: (event) => {
              dispatch({ type: "stream_event", turnId, event, now: Date.now() });
              if (event.event === "a2a_result") {
                void qc.invalidateQueries({ queryKey: ["ledger"] });
                void qc.invalidateQueries({ queryKey: queryKeys.wallet });
              }
              if (event.event === "done" && "mandate" in event.data && event.data.mandate) sawMandateCounters = true;
            },
          },
        );
      } catch (err) {
        if (!controller.signal.aborted) {
          const code = isApiError(err) ? err.code : ErrorCodes.internal_error;
          dispatch({
            type: "turn_failed",
            turnId,
            error: { code, message: errorMessage(err), traceId: errorTraceId(err) },
            now: Date.now(),
          });
          if (MANDATE_DEAD_CODES.has(code)) {
            setActive((prev) => (prev && prev.mandate.id === mandateId ? { ...prev, dead: code } : prev));
          }
        }
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
          setStreamingTurn(null);
        }
        refreshMoney();
        if (!sawMandateCounters) {
          // Lost connection or stopped: the server may still settle an in-flight call, so re-read.
          if (controller.signal.aborted) window.setTimeout(() => void syncMandate(mandateId), 1500);
          else void syncMandate(mandateId);
        }
      }
    },
    [qc, refreshMoney, syncMandate],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreamingTurn(null);
    dispatch({ type: "reset_turns" });
  }, []);

  return {
    turns: state.turns,
    counters: state.counters,
    active,
    streaming: streamingTurn !== null,
    authorised,
    markRevoked,
    forgetMandate,
    send,
    stop,
    reset,
  };
}
