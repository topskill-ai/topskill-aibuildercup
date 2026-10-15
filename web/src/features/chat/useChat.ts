/**
 * Chat session hook for the agent page (SPEC §7, §11.4):
 * - streams a turn with `streamSSE<ChatStreamEvent>` and folds events into `chatReducer`;
 * - persists the conversation id per tab (`topskill.chat.<slug>`) plus a transcript stash, and
 *   restores both on load (`GET …/conversations/{id}`; `session_not_owned` starts over);
 * - Stop aborts the fetch and calls the abort endpoint, then reconciles with the server, because a
 *   stop that arrives after the model finished is still a charged round (§7.4);
 * - maps pre-stream errors to UI gates (paywall, temporarily unavailable) by `code` only.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { ApiError, ErrorCodes, hasCode } from "@/api/errors";
import { queryKeys } from "@/api/queryClient";
import { streamSSE } from "@/api/sse";
import type { ChatStreamEvent, PublicAgent } from "@/api/types";
import { useLoginDialog } from "@/components/LoginDialog";
import { useAuth } from "@/lib/auth";
import { toast, toastApiError } from "@/lib/toast";
import { abortTurn, chatPath, fetchConversation } from "./api";
import { chatReducer, initialChatState, toWire } from "./chatState";
import {
  clearConversation,
  readConversationId,
  readTranscript,
  writeConversationId,
  writeTranscript,
} from "./storage";

/** Delays (ms) at which a stopped / connection-lost turn is re-read from the server. */
const RECONCILE_DELAYS_MS = [1500, 4000];

export type ChatGate =
  | { kind: "paywall"; error: ApiError | null; text: string | null }
  | { kind: "unavailable"; error: ApiError };

interface ActiveTurn {
  turnId: string;
  controller: AbortController;
  statusCid: string | null;
  stopped: boolean;
  doneSeen: boolean;
  lost: boolean;
}

/** Client turn id: `/^[A-Za-z0-9_-]{8,64}$/` (SPEC §6.4). */
export function newTurnId(): string {
  const bytes = new Uint8Array(16);
  try {
    crypto.getRandomValues(bytes);
  } catch {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  }
  return `t_${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

export function useChat(slug: string, agent: Pick<PublicAgent, "title">) {
  const auth = useAuth();
  const { ensureSignedIn } = useLoginDialog();
  const qc = useQueryClient();
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const [conversationId, setConversationIdState] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [gate, setGate] = useState<ChatGate | null>(null);
  const cidRef = useRef<string | null>(null);
  const activeRef = useRef<ActiveTurn | null>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  /** Bumped by "start over": pending reconciles of the previous conversation are dropped. */
  const generation = useRef(0);

  const uid = auth.status === "signed_in" ? (auth.me?.uid ?? null) : null;

  // Restore (or reset) whenever the signed-in account changes. Adjusting state during render
  // keeps the first paint consistent (no flash of the cold-start screen before the stash).
  const [restoredFor, setRestoredFor] = useState<string | null>(null);
  if (uid !== null && restoredFor === null && (state.activeTurnId !== null || state.messages.length > 0)) {
    // Signed in from the login dialog opened by `send`: the profile re-render can land after
    // `ensureSignedIn` resolved and the turn started (or even finished). While signed out the
    // transcript is always empty, so any message here belongs to that turn: keep it (it opened a
    // new conversation) instead of restoring over it.
    setRestoredFor(uid);
  } else if (uid !== restoredFor && (uid !== null || auth.status === "signed_out")) {
    setRestoredFor(uid);
    const cid = uid ? readConversationId(slug) : null;
    const stash = uid && cid ? readTranscript(slug, cid, uid) : null;
    dispatch({ type: "reset", messages: stash ?? [] });
    setConversationIdState(cid);
    setRestoring(Boolean(cid));
  }

  useEffect(() => {
    cidRef.current = conversationId;
  }, [conversationId]);

  const setConversation = useCallback(
    (cid: string | null) => {
      cidRef.current = cid;
      setConversationIdState(cid);
      if (cid) writeConversationId(slug, cid);
    },
    [slug],
  );

  const startOver = useCallback(() => {
    generation.current += 1;
    for (const t of timers.current) clearTimeout(t);
    timers.current = [];
    clearConversation(slug);
    setConversation(null);
    dispatch({ type: "reset", messages: [] });
  }, [slug, setConversation]);

  // Server restore of the stored conversation.
  useEffect(() => {
    if (!restoring || !uid || !conversationId) return;
    const ctrl = new AbortController();
    fetchConversation(slug, conversationId, ctrl.signal)
      .then((conv) => {
        if (ctrl.signal.aborted || activeRef.current) return;
        dispatch({ type: "reset", messages: conv.messages });
      })
      .catch((err: unknown) => {
        // A turn sent meanwhile re-validates the id itself (404 `session_not_owned` → start over).
        if (ctrl.signal.aborted || activeRef.current) return;
        if (hasCode(err, ErrorCodes.session_not_owned, ErrorCodes.not_found)) {
          startOver();
          toast.info("Started a new conversation for this account");
        }
        // Other failures keep the stashed transcript; the next turn re-validates the id.
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setRestoring(false);
      });
    return () => ctrl.abort();
  }, [restoring, uid, conversationId, slug, startOver]);

  // Keep the per-tab transcript stash current (only settled transcripts).
  useEffect(() => {
    if (!uid || !conversationId || state.activeTurnId) return;
    writeTranscript(slug, conversationId, uid, state.messages.filter((m) => !m.streaming).map(toWire));
  }, [slug, uid, conversationId, state.messages, state.activeTurnId]);

  useEffect(
    () => () => {
      for (const t of timers.current) clearTimeout(t);
      activeRef.current?.controller.abort();
    },
    [],
  );

  const refreshBilling = useCallback(() => {
    void qc.invalidateQueries({ queryKey: queryKeys.billing(slug) });
    void qc.invalidateQueries({ queryKey: queryKeys.wallet });
    // The info panel's conversation count changes after a first turn.
    void qc.invalidateQueries({ queryKey: queryKeys.publicAgent(slug) });
  }, [qc, slug]);

  /**
   * After a stop or a lost connection the server decides the outcome (it may have settled the
   * round as `ok`). Re-read the conversation and adopt it once it contains this turn.
   */
  const reconcile = useCallback(
    (turnId: string, statusCid: string | null) => {
      const gen = generation.current;
      const stale = () => activeRef.current !== null || generation.current !== gen;
      const attemptAt = (attempt: number) => {
        if (attempt >= RECONCILE_DELAYS_MS.length) return;
        const timer = setTimeout(() => {
          timers.current = timers.current.filter((t) => t !== timer);
          if (stale()) return;
          const cid = cidRef.current ?? statusCid;
          if (!cid) return;
          fetchConversation(slug, cid)
            .then((conv) => {
              if (stale()) return;
              if (!conv.messages.some((m) => m.turn_id === turnId && m.role === "model")) {
                attemptAt(attempt + 1);
                return;
              }
              dispatch({ type: "reset", messages: conv.messages });
              setConversation(cid);
              refreshBilling();
            })
            .catch(() => attemptAt(attempt + 1));
        }, RECONCILE_DELAYS_MS[attempt]);
        timers.current.push(timer);
      };
      attemptAt(0);
    },
    [slug, setConversation, refreshBilling],
  );

  /**
   * Maps a pre-stream failure to UI. Returns true when the draft may be cleared (the agent is
   * unavailable). The paywall keeps the draft in the composer too, so closing it loses nothing.
   */
  const handleSendError = useCallback(
    (err: unknown, text: string): boolean => {
      if (hasCode(err, ErrorCodes.visitor_insufficient_points)) {
        setGate({ kind: "paywall", error: err, text });
        refreshBilling();
        return false;
      }
      if (hasCode(err, ErrorCodes.insufficient_points, ErrorCodes.agent_offline, ErrorCodes.agent_not_found)) {
        setGate({ kind: "unavailable", error: err });
        return true;
      }
      if (hasCode(err, ErrorCodes.session_not_owned)) {
        startOver();
        toast.info("Started a new conversation for this account");
        return false;
      }
      if (hasCode(err, ErrorCodes.conversation_limit_reached)) {
        toast.error(err.message || "This conversation reached its limit.", {
          description: err.traceId ? `Trace ID: ${err.traceId}` : undefined,
          action: { label: "New conversation", onClick: () => startOver() },
        });
        return false;
      }
      if (hasCode(err, ErrorCodes.turn_in_progress)) {
        toastApiError(err, "A reply is still being written in this conversation");
        return false;
      }
      toastApiError(err);
      return false;
    },
    [refreshBilling, startOver],
  );

  /**
   * Send one message. Resolves true when the composer may clear (the turn started); false keeps
   * the draft (not signed in, paywall, other errors).
   */
  const send = useCallback(
    async (raw: string): Promise<boolean> => {
      const text = raw.trim();
      if (!text || activeRef.current) return false;
      const signedIn = await ensureSignedIn({ reason: `Sign in to chat with ${agent.title}.` });
      if (!signedIn || activeRef.current) return false;

      setGate(null);
      const turnId = newTurnId();
      const active: ActiveTurn = {
        turnId,
        controller: new AbortController(),
        statusCid: null,
        stopped: false,
        doneSeen: false,
        lost: false,
      };
      activeRef.current = active;
      dispatch({ type: "start", turnId, text, now: new Date().toISOString() });

      try {
        await streamSSE<ChatStreamEvent>(
          chatPath(slug),
          { conversation_id: cidRef.current, turn_id: turnId, message: text },
          {
            signal: active.controller.signal,
            onEvent: (event) => {
              if (event.event === "status") active.statusCid = event.data.conversation_id;
              if (event.event === "error" && event.data.code === ErrorCodes.connection_lost) active.lost = true;
              if (event.event === "done") {
                active.doneSeen = true;
                // null = nothing persisted for a new conversation: never keep the minted id.
                if (event.data.conversation_id) setConversation(event.data.conversation_id);
                activeRef.current = null;
              }
              dispatch({ type: "event", turnId, event });
            },
          },
        );
      } catch (err) {
        activeRef.current = null;
        dispatch({ type: "cancel", turnId });
        return handleSendError(err, text);
      }

      activeRef.current = null;
      if (!active.doneSeen) dispatch({ type: "stopped", turnId });
      refreshBilling();
      if (active.stopped || active.lost || !active.doneSeen) reconcile(turnId, active.statusCid);
      return true;
    },
    [agent.title, ensureSignedIn, slug, setConversation, handleSendError, refreshBilling, reconcile],
  );

  const stop = useCallback(() => {
    const a = activeRef.current;
    if (!a || a.stopped) return;
    a.stopped = true;
    const cid = a.statusCid ?? cidRef.current;
    if (cid) abortTurn(slug, cid, a.turnId).catch(() => undefined);
    a.controller.abort();
  }, [slug]);

  const newConversation = useCallback(() => {
    if (activeRef.current) return;
    startOver();
    setGate(null);
  }, [startOver]);

  return {
    messages: state.messages,
    streaming: state.activeTurnId !== null,
    restoring,
    conversationId,
    gate,
    openTopUp: () => setGate({ kind: "paywall", error: null, text: null }),
    closeGate: () => setGate(null),
    send,
    stop,
    newConversation,
  };
}

export type ChatController = ReturnType<typeof useChat>;
