import { CircleCheck, CircleSlash, Coins, WifiOff } from "lucide-react";
import { useCallback, useState } from "react";
import type { Citation } from "@/api/types";
import { Markdown } from "@/components/Markdown";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { GuardNote } from "@/features/guard/GuardBits";
import { cn } from "@/lib/cn";
import { chargeLabel, completionAnnouncement, type ChargeContext, type ViewMessage } from "./chatState";
import { CitationList, SearchEntryPoint } from "./CitationList";
import { citationDomId } from "./display";
import { ToolActivity } from "./ToolActivity";

function UserBubble({ m }: { m: ViewMessage }) {
  return (
    <div className="flex flex-col items-end">
      <div className="max-w-[85%] min-w-0 rounded-card rounded-br-[4px] bg-primary px-4 py-2.5 text-[15px] whitespace-pre-wrap text-primary-fg [overflow-wrap:anywhere]">
        {m.text}
      </div>
      {m.guard ? <GuardNote className="mt-1 max-w-[85%] text-right" guard={m.guard} /> : null}
    </div>
  );
}

function ChargeFooter({ m, ctx }: { m: ViewMessage; ctx: ChargeContext }) {
  const label = chargeLabel(m, ctx);
  if (!label) return null;
  const lost = m.error?.code === "connection_lost";
  const charged = (m.charge?.charged_cents ?? 0) > 0 && m.status === "ok";
  const Icon = lost ? WifiOff : charged ? Coins : m.status === "ok" ? CircleCheck : CircleSlash;
  return (
    <div
      className={cn("mt-2 flex items-center gap-1.5 text-xs", charged ? "text-muted" : "text-subtle")}
      data-testid="charge-footer"
    >
      <Icon aria-hidden className="h-3.5 w-3.5" />
      <span className="font-mono tabular-nums">{label}</span>
    </div>
  );
}

function ModelMessage({ m, ctx }: { m: ViewMessage; ctx: ChargeContext }) {
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const onCitationClick = useCallback(
    (c: Citation) => {
      setHighlighted(c.n);
      const el = document.getElementById(citationDomId(m.id, c.n));
      el?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
      el?.focus({ preventScroll: true });
    },
    [m.id],
  );

  const empty = !m.text.trim();
  return (
    <article className="min-w-0" aria-busy={m.streaming || undefined} data-testid="model-message">
      <ToolActivity tools={m.tools} className="mb-2" />
      {!empty || m.streaming ? (
        <Markdown text={m.text} citations={m.citations} onCitationClick={onCitationClick} streaming={m.streaming} />
      ) : m.status === "failed" ? (
        <p className="text-sm text-muted italic">No answer was produced for this message. You were not charged.</p>
      ) : m.status === "aborted" && !m.error ? (
        <p className="text-sm text-muted italic">Stopped before an answer was written.</p>
      ) : null}
      {m.guard ? <GuardNote className="mt-2" guard={m.guard} /> : null}
      {m.error ? (
        <ErrorLine className="mt-2" message={m.error.message || "Something went wrong."} traceId={m.error.trace_id} />
      ) : null}
      {!m.streaming && m.citations.length > 0 ? (
        <CitationList className="mt-3" messageId={m.id} citations={m.citations} highlighted={highlighted} />
      ) : null}
      {!m.streaming && m.search_entry_point_html ? (
        <SearchEntryPoint className="mt-3" html={m.search_entry_point_html} />
      ) : null}
      <ChargeFooter m={m} ctx={ctx} />
    </article>
  );
}

/**
 * Transcript: user bubbles right, model answers full-width Markdown (SPEC §11.4). The transcript
 * itself is not a live region (it re-renders on every delta); a hidden status line announces
 * when an answer is complete instead.
 */
export function MessageList({ messages, ctx }: { messages: ViewMessage[]; ctx: ChargeContext }) {
  return (
    <>
      <ol className="space-y-6" aria-label="Conversation">
        {messages.map((m) => (
          <li key={`${m.turn_id}-${m.role}`} className="min-w-0">
            {m.role === "user" ? <UserBubble m={m} /> : <ModelMessage m={m} ctx={ctx} />}
          </li>
        ))}
      </ol>
      <p role="status" className="sr-only" data-testid="chat-announcer">
        {completionAnnouncement(messages, ctx)}
      </p>
    </>
  );
}
