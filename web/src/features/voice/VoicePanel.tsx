import { Mic, MicOff, PhoneOff, RotateCcw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { cn } from "@/lib/cn";
import { formatCredits } from "@/lib/money";
import { ToolActivity } from "@/features/chat/ToolActivity";
import type { VoiceController } from "./useVoice";
import {
  closedMessage,
  formatClock,
  isActive,
  voiceActivity,
  type CaptionTurn,
  type VoiceActivity,
  type VoiceState,
} from "./voiceState";

const ACTIVITY_LABEL: Record<VoiceActivity, string> = {
  listening: "Listening",
  thinking: "Thinking…",
  speaking: "Speaking",
};

function statusLabel(state: VoiceState): string {
  switch (state.phase) {
    case "connecting":
      return "Connecting…";
    case "ending":
      return "Ending call…";
    case "ended":
      return "Call ended";
    case "error":
      return "Call failed";
    case "live":
      return ACTIVITY_LABEL[voiceActivity(state)];
    default:
      return "";
  }
}

function useElapsed(startedAt: number | null, running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [running]);
  return startedAt ? Math.max(0, (now - startedAt) / 1000) : 0;
}

/** Pulsing orb: colour by activity, scaled by the live mic level while listening. */
function Orb({ state, voice }: { state: VoiceState; voice: VoiceController }) {
  const ring = useRef<HTMLSpanElement | null>(null);
  const activity = voiceActivity(state);
  const live = state.phase === "live";
  useEffect(() => {
    voice.onLevel((level) => {
      const el = ring.current;
      if (el) el.style.transform = `scale(${1 + Math.min(1, level * 6) * 0.6})`;
    });
    return () => voice.onLevel(() => undefined);
  }, [voice]);
  return (
    <span className="relative flex h-10 w-10 shrink-0 items-center justify-center" aria-hidden>
      <span
        ref={ring}
        className={cn(
          "absolute inset-0 rounded-full transition-transform duration-100",
          !live && "opacity-40",
          activity === "speaking" ? "bg-emerald-100" : activity === "thinking" ? "bg-amber-100" : "bg-primary-soft",
        )}
      />
      <span
        className={cn(
          "relative h-4 w-4 rounded-full",
          state.phase === "error"
            ? "bg-danger"
            : !live
              ? "bg-subtle"
              : activity === "speaking"
                ? "animate-pulse bg-success"
                : activity === "thinking"
                  ? "animate-pulse bg-warning"
                  : "bg-primary",
        )}
      />
    </span>
  );
}

function Caption({ turn, agentTitle }: { turn: CaptionTurn; agentTitle: string }) {
  return (
    <li className="space-y-1.5" data-testid="voice-caption">
      {turn.user.trim() ? (
        <p className="text-sm text-muted [overflow-wrap:anywhere]">
          <span className="font-medium text-text">You: </span>
          {turn.user.trim()}
        </p>
      ) : null}
      {turn.tools.length > 0 ? <ToolActivity tools={turn.tools} /> : null}
      {turn.model.trim() ? (
        <p className="text-[15px] text-text [overflow-wrap:anywhere]">
          <span className="font-medium">{agentTitle}: </span>
          {turn.model.trim()}
          {turn.interrupted && turn.outcome !== "ok" ? <span className="text-subtle"> (interrupted)</span> : null}
        </p>
      ) : null}
      {turn.outcome && turn.billing ? (
        <p className="font-mono text-2xs text-subtle tabular-nums" data-testid="voice-charge">
          {turn.outcome !== "ok"
            ? "Not charged"
            : turn.billing.charged_cents > 0
              ? `Charged ${formatCredits(turn.billing.charged_cents)} · balance ${formatCredits(turn.billing.balance_cents)}`
              : "Free round"}
        </p>
      ) : null}
    </li>
  );
}

export interface VoicePanelProps {
  voice: VoiceController;
  agentTitle: string;
  className?: string;
}

/**
 * The live call strip that replaces the composer while voice mode is on: state, timer, live captions
 * (last two rounds), mute and hang-up; errors keep their trace id (SPEC §11.3).
 */
export function VoicePanel({ voice, agentTitle, className }: VoicePanelProps) {
  const { state } = voice;
  const active = isActive(state);
  const elapsed = useElapsed(state.startedAt, state.phase === "live");
  const recent = state.turns.filter((t) => t.user.trim() || t.model.trim() || t.tools.length).slice(-2);
  const ended = closedMessage(state.closedReason);

  return (
    <section
      aria-label="Voice call"
      className={cn("min-w-0 rounded-card border border-border bg-surface p-3 sm:p-4", className)}
      data-testid="voice-panel"
    >
      <div className="flex min-w-0 items-center gap-3">
        <Orb state={state} voice={voice} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium" role="status" data-testid="voice-status">
            {statusLabel(state)}
          </p>
          <p className="truncate font-mono text-2xs text-subtle tabular-nums">
            {state.phase === "live" || state.phase === "ending"
              ? `${formatClock(elapsed)}${state.maxSessionS ? ` / ${formatClock(state.maxSessionS)}` : ""}`
              : state.phase === "connecting"
                ? "Allow the microphone if your browser asks"
                : state.rounds > 0
                  ? `${state.rounds} answered ${state.rounds === 1 ? "question" : "questions"}`
                  : "Voice call"}
          </p>
        </div>
        {active ? (
          <>
            <Button
              variant="outline"
              size="icon"
              aria-pressed={state.muted}
              aria-label={state.muted ? "Unmute microphone" : "Mute microphone"}
              disabled={state.phase !== "live"}
              onClick={() => voice.setMuted(!state.muted)}
            >
              {state.muted ? <MicOff aria-hidden className="h-4 w-4" /> : <Mic aria-hidden className="h-4 w-4" />}
            </Button>
            <Button
              variant="danger"
              className="h-9"
              onClick={voice.end}
              disabled={state.phase === "ending"}
              icon={<PhoneOff aria-hidden className="h-4 w-4" />}
            >
              <span className="hidden sm:inline">End call</span>
              <span className="sm:hidden">End</span>
            </Button>
          </>
        ) : (
          <Button variant="outline" size="sm" onClick={voice.reset} icon={<X aria-hidden className="h-3.5 w-3.5" />}>
            Close
          </Button>
        )}
      </div>

      {recent.length > 0 ? (
        <ol
          className="mt-3 max-h-[38vh] space-y-3 overflow-y-auto border-t border-border pt-3"
          aria-label="Live captions"
          aria-live="polite"
        >
          {recent.map((t) => (
            <Caption key={t.turnId} turn={t} agentTitle={agentTitle} />
          ))}
        </ol>
      ) : state.phase === "live" ? (
        <p className="mt-3 border-t border-border pt-3 text-sm text-muted">
          Go ahead and ask your question. You can interrupt the answer at any time.
        </p>
      ) : null}

      {state.phase === "error" && state.error ? (
        <div className="mt-3 border-t border-border pt-3">
          <ErrorLine message={state.error.message} traceId={state.error.traceId || undefined} />
          <Button
            variant="outline"
            size="sm"
            className="mt-2"
            onClick={() => void voice.start()}
            icon={<RotateCcw aria-hidden className="h-3.5 w-3.5" />}
          >
            Try again
          </Button>
        </div>
      ) : null}
      {state.phase === "ended" && ended ? <p className="mt-3 text-sm text-muted">{ended}</p> : null}
    </section>
  );
}

export interface VoiceButtonProps {
  onClick: () => void;
  disabled?: boolean;
}

/** Mic button next to the composer: starts a voice call. */
export function VoiceButton({ onClick, disabled }: VoiceButtonProps) {
  return (
    <Button
      variant="outline"
      className="h-11 w-11 p-0"
      aria-label="Start a voice call"
      title="Talk to this agent"
      disabled={disabled}
      onClick={onClick}
      data-testid="voice-start"
    >
      <Mic aria-hidden className="h-4 w-4" />
    </Button>
  );
}
