import {
  ArrowRight,
  Ban,
  Bot,
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  ExternalLink,
  Lock,
  Network,
  Search,
  ShieldAlert,
  Sparkles,
  Wrench,
  XCircle,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { MANDATE_ERROR_CODES } from "@/api/errors";
import type { DiscoveredAgent } from "@/api/types";
import { Markdown } from "@/components/Markdown";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Money } from "@/components/ui/Money";
import { Spinner } from "@/components/ui/Spinner";
import { Tooltip } from "@/components/ui/Tooltip";
import { callErrorText, useNow } from "@/features/mandates/model";
import { cn } from "@/lib/cn";
import { formatCredits } from "@/lib/money";
import { useAgentLook } from "@/features/mandates/agentLook";
import { summarizeTurn, type CallItem, type DiscoverItem, type TimelineItem, type ToolItem, type Turn } from "./timeline";

// ---------------------------------------------------------------------------------------------
// Layout primitives

type NodeTone = "primary" | "success" | "danger" | "warning" | "neutral";

const NODE_TONES: Record<NodeTone, string> = {
  primary: "bg-primary text-white",
  success: "bg-success text-white",
  danger: "bg-danger text-white",
  warning: "bg-warning text-white",
  neutral: "bg-slate-200 text-muted",
};

function Node({ icon, tone, pulse, children }: { icon: ReactNode; tone: NodeTone; pulse?: boolean; children: ReactNode }) {
  return (
    <li className="relative animate-fade-in pb-5 pl-11 last:pb-0">
      <span
        aria-hidden
        className={cn(
          "absolute left-0 top-0 flex h-8 w-8 items-center justify-center rounded-full ring-4 ring-bg [&_svg]:h-4 [&_svg]:w-4",
          NODE_TONES[tone],
        )}
      >
        {pulse ? <span className="absolute inset-0 animate-ping rounded-full bg-primary/30" /> : null}
        <span className="relative">{icon}</span>
      </span>
      {children}
    </li>
  );
}

// ---------------------------------------------------------------------------------------------
// Discovery

function DiscoveredAgentCard({ agent }: { agent: DiscoveredAgent }) {
  const look = useAgentLook(agent.slug);
  return (
    <div className="flex min-w-0 items-start gap-3 rounded-control border border-border bg-surface p-3">
      <Avatar src={look.avatarUrl} name={agent.name} accent={look.accent} size={36} ring />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold">{agent.name}</div>
        <div className="line-clamp-2 text-[13px] leading-5 text-muted">{agent.tagline}</div>
        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          <Badge variant="outline">
            <span className="font-mono tabular-nums">{formatCredits(agent.price_cents)}</span> / call
          </Badge>
          <a
            href={agent.card_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-2xs font-medium text-primary hover:underline"
          >
            Agent Card <ExternalLink aria-hidden className="h-3 w-3" />
          </a>
        </div>
      </div>
    </div>
  );
}

function DiscoverNode({ item }: { item: DiscoverItem }) {
  const running = item.status === "running";
  const failed = item.status === "error" && item.agents.length === 0;
  return (
    <Node icon={<Search />} tone={failed ? "danger" : running ? "primary" : "primary"} pulse={running}>
      <div className="flex flex-wrap items-baseline gap-x-2 pt-1">
        <span className="font-semibold">
          {running
            ? "Searching the TopSkill directory…"
            : failed
              ? "Agent directory unavailable"
              : `Discovered ${item.agents.length} ${item.agents.length === 1 ? "agent" : "agents"}`}
        </span>
        {item.query ? <span className="min-w-0 truncate text-[13px] text-muted">for “{item.query}”</span> : null}
      </div>
      {item.agents.length > 0 ? (
        <>
          <p className="mt-0.5 text-[13px] text-subtle">Read from each agent’s A2A Agent Card.</p>
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-[repeat(auto-fill,minmax(min(100%,220px),1fr))]">
            {item.agents.map((a) => (
              <DiscoveredAgentCard key={a.slug} agent={a} />
            ))}
          </div>
        </>
      ) : running ? (
        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton-shimmer h-[86px] rounded-control" />
          ))}
        </div>
      ) : null}
    </Node>
  );
}

// ---------------------------------------------------------------------------------------------
// A2A call

const CALL_STYLE: Record<CallItem["status"], { tone: NodeTone; card: string }> = {
  pending: { tone: "primary", card: "border-primary/30" },
  running: { tone: "primary", card: "border-primary/40 shadow-[0_0_0_4px_rgba(79,70,229,0.06)]" },
  completed: { tone: "success", card: "border-success/30" },
  rejected: { tone: "danger", card: "border-danger/40 bg-danger-soft/40" },
  failed: { tone: "danger", card: "border-danger/30" },
  unknown: { tone: "warning", card: "border-warning/40" },
};

function Elapsed({ since }: { since: number }) {
  const now = useNow(500);
  return <span className="font-mono tabular-nums">{Math.max(0, (now - since) / 1000).toFixed(1)}s</span>;
}

function CallStatusLine({ item }: { item: CallItem }) {
  switch (item.status) {
    case "pending":
      return (
        <div className="flex items-center gap-2 text-sm text-primary">
          <Spinner size={14} /> Checking the mandate…
        </div>
      );
    case "running":
      return (
        <div className="flex items-center gap-2 text-sm text-primary">
          <Spinner size={14} /> Calling over A2A · <Elapsed since={item.startedAt} />
        </div>
      );
    case "completed":
      return (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <span className="flex items-center gap-1.5 font-semibold text-success">
            <CheckCircle2 aria-hidden className="h-4 w-4" /> Completed
          </span>
          <span className="text-subtle">·</span>
          <span className="font-medium">
            Charged <Money cents={item.chargedCents} />
          </span>
          {item.creatorShareCents > 0 ? (
            <span className="text-[13px] text-muted">
              (creator earns <Money cents={item.creatorShareCents} unit={false} />)
            </span>
          ) : null}
        </div>
      );
    case "rejected":
    case "failed":
      return (
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm" data-testid="call-rejection">
            <span className="flex items-center gap-1.5 font-semibold text-danger">
              {item.status === "rejected" ? <Ban aria-hidden className="h-4 w-4" /> : <XCircle aria-hidden className="h-4 w-4" />}
              {item.status === "rejected" ? "Rejected" : "Failed"}
            </span>
            {item.errorCode ? (
              <>
                <span className="text-subtle">·</span>
                <code className="rounded-[6px] bg-danger-soft px-1.5 py-0.5 font-mono text-xs text-danger">{item.errorCode}</code>
              </>
            ) : null}
            <span className="text-subtle">·</span>
            {item.chargedCents > 0 ? (
              <span className="font-medium">
                Charged <Money cents={item.chargedCents} />
              </span>
            ) : (
              <span className="font-semibold text-text">Not charged</span>
            )}
          </div>
          <p className="text-[13px] text-muted">{callErrorText(item.errorCode)}.</p>
        </div>
      );
    default:
      return (
        <div className="flex items-center gap-1.5 text-sm text-warning">
          <CircleHelp aria-hidden className="h-4 w-4" /> Result unknown — the connection ended first. Check the wallet ledger.
        </div>
      );
  }
}

function MandateBadge({ item }: { item: CallItem }) {
  const mandateRejected = item.status === "rejected" && item.errorCode && MANDATE_ERROR_CODES.includes(item.errorCode);
  if (mandateRejected)
    return (
      <Badge variant="danger" icon={<ShieldAlert aria-hidden className="h-3 w-3" />}>
        Blocked by mandate
      </Badge>
    );
  if (!item.sent) return <Badge variant="neutral">Not sent</Badge>;
  if (item.callId === null && item.status === "rejected") return <Badge variant="neutral">Not sent</Badge>;
  return (
    <Tooltip content="Signed Ed25519 intent mandate attached as X-AP2-Mandate and verified by the agent's endpoint before it ran.">
      <span tabIndex={0} className="inline-flex rounded-chip">
        <Badge variant="success" icon={<Lock aria-hidden className="h-3 w-3" />}>
          Verified mandate
        </Badge>
      </span>
    </Tooltip>
  );
}

function CallNode({ item }: { item: CallItem }) {
  const look = useAgentLook(item.slug);
  const [open, setOpen] = useState(false);
  const style = CALL_STYLE[item.status];
  const name = item.agentName || look.title || item.slug;
  const active = item.status === "pending" || item.status === "running";
  return (
    <Node
      icon={item.status === "completed" ? <CheckCircle2 /> : item.status === "rejected" ? <Ban /> : <Network />}
      tone={style.tone}
      pulse={active}
    >
      <div className="pt-1 text-[13px] font-medium text-muted">
        A2A call <ArrowRight aria-hidden className="inline h-3.5 w-3.5" /> <span className="text-text">{name}</span>
      </div>
      <article
        aria-label={`A2A call to ${name}`}
        data-status={item.status}
        className={cn("relative mt-2 overflow-hidden rounded-card border bg-surface transition-colors duration-300", style.card)}
      >
        {active ? <div aria-hidden className="skeleton-shimmer absolute inset-x-0 top-0 h-0.5 opacity-80" /> : null}
        <div className="flex flex-wrap items-start justify-between gap-3 p-4">
          <div className="flex min-w-0 items-center gap-3">
            <Avatar src={look.avatarUrl} name={name} accent={look.accent} size={40} ring />
            <div className="min-w-0">
              <div className="truncate font-semibold">{name}</div>
              <div className="truncate font-mono text-2xs text-subtle">{item.slug}</div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <MandateBadge item={item} />
            {item.priceCents !== null ? (
              <Badge variant="outline">
                <span className="font-mono tabular-nums">{formatCredits(item.priceCents)}</span> / call
              </Badge>
            ) : null}
          </div>
        </div>
        <div className="space-y-3 border-t border-border/70 px-4 py-3">
          <CallStatusLine item={item} />
          {item.requestPreview ? (
            <p className="line-clamp-2 border-l-2 border-border pl-3 text-[13px] text-muted" title={item.requestPreview}>
              {item.requestPreview}
            </p>
          ) : null}
          {item.answerPreview ? (
            <div>
              <button
                type="button"
                aria-expanded={open}
                onClick={() => setOpen((o) => !o)}
                className="inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline"
              >
                <ChevronDown aria-hidden className={cn("h-4 w-4 transition-transform", open && "rotate-180")} />
                {open ? "Hide answer" : `Show ${name}’s answer`}
              </button>
              {open ? (
                <div className="mt-2 rounded-control bg-slate-50 p-3 text-sm">
                  <Markdown text={item.answerPreview} />
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
        {item.callId === null && (item.status === "rejected" || !item.sent) ? (
          <div className="border-t border-border/70 bg-slate-50/70 px-4 py-1.5 text-2xs text-subtle">
            Stopped by the pre-flight check — no request was sent to the agent.
          </div>
        ) : item.endpoint ? (
          <div className="truncate border-t border-border/70 bg-slate-50/70 px-4 py-1.5 font-mono text-2xs text-subtle" title={item.endpoint}>
            JSON-RPC SendMessage → {item.endpoint}
          </div>
        ) : null}
      </article>
    </Node>
  );
}

function ToolNode({ item }: { item: ToolItem }) {
  return (
    <Node icon={<Wrench />} tone={item.status === "error" ? "danger" : "neutral"}>
      <div className="pt-1 text-sm">
        <span className="font-medium">{item.title}</span>
        {item.target ? <span className="text-muted"> · {item.target}</span> : null}
      </div>
    </Node>
  );
}

function ItemNode({ item }: { item: TimelineItem }) {
  if (item.kind === "discover") return <DiscoverNode item={item} />;
  if (item.kind === "call") return <CallNode item={item} />;
  return <ToolNode item={item} />;
}

// ---------------------------------------------------------------------------------------------
// Turn

function TurnSummaryBar({ turn }: { turn: Turn }) {
  const s = summarizeTurn(turn);
  if (s.calls === 0 && s.discovered === 0) return null;
  const parts: ReactNode[] = [];
  if (s.discovered) parts.push(<span key="d">{s.discovered} discovered</span>);
  if (s.completed) parts.push(<span key="c" className="text-success">{s.completed} paid</span>);
  if (s.rejected) parts.push(<span key="r" className="text-danger">{s.rejected} rejected</span>);
  if (s.failed) parts.push(<span key="f" className="text-danger">{s.failed} failed</span>);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-control border border-border bg-surface px-3 py-2 text-[13px] text-muted">
      {parts.map((p, i) => (
        <span key={i} className="flex items-center gap-3">
          {i > 0 ? <span aria-hidden className="text-subtle">·</span> : null}
          {p}
        </span>
      ))}
      <span aria-hidden className="text-subtle">·</span>
      <span className="font-medium text-text">
        Spent <Money cents={s.spentCents} />
      </span>
    </div>
  );
}

export interface TurnViewProps {
  turn: Turn;
  /** Shown when a pre-stream mandate error asks for a new budget; receives the task to retry. */
  onReauthorise?: (task: string) => void;
}

export function TurnView({ turn, onReauthorise }: TurnViewProps) {
  const streaming = turn.status === "streaming";
  const busyItem = turn.items.some(
    (i) => (i.kind === "discover" && i.status === "running") || (i.kind === "call" && (i.status === "pending" || i.status === "running")),
  );
  const thinking = streaming && !turn.answer && !busyItem;
  const mandateError = turn.error && (MANDATE_ERROR_CODES.includes(turn.error.code) || turn.error.code === "forbidden");
  return (
    <section aria-label="Assistant turn" className="space-y-4">
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-card rounded-br-[4px] bg-primary px-4 py-2.5 text-[15px] text-primary-fg whitespace-pre-wrap break-words">
          {turn.userText}
        </div>
      </div>

      {turn.items.length > 0 || thinking ? (
        <div className="relative">
          <div aria-hidden className="absolute bottom-4 left-[15px] top-4 w-px bg-border" />
          <ol className="relative">
            {turn.items.map((item) => (
              <ItemNode key={item.key} item={item} />
            ))}
            {thinking ? (
              <Node icon={<Sparkles />} tone="primary" pulse>
                <div className="pt-1 text-sm text-muted">
                  {turn.items.length === 0 ? "Planning which specialists to hire…" : "Deciding the next step…"}
                </div>
              </Node>
            ) : null}
          </ol>
        </div>
      ) : null}

      {turn.answer ? (
        <div className="rounded-card border border-border bg-surface">
          <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-500 text-white">
              <Bot aria-hidden className="h-3.5 w-3.5" />
            </span>
            <span className="text-sm font-semibold">Enterprise Assistant</span>
            {turn.model ? <span className="ml-auto truncate font-mono text-2xs text-subtle">{turn.model}</span> : null}
          </div>
          <div className="px-4 py-3">
            <Markdown text={turn.answer} streaming={streaming} />
          </div>
        </div>
      ) : null}

      {turn.status === "aborted" && !turn.error ? <p className="text-[13px] text-subtle">Stopped.</p> : null}

      {turn.error ? (
        <div className="rounded-card border border-danger/30 bg-danger-soft/50 p-3">
          <ErrorLine message={turn.error.message || "Something went wrong."} traceId={turn.error.traceId} />
          {mandateError && turn.rejectedBeforeStream && onReauthorise ? (
            <Button size="sm" variant="outline" className="mt-2" icon={<Lock aria-hidden className="h-4 w-4" />} onClick={() => onReauthorise(turn.userText)}>
              Authorise a new budget and retry
            </Button>
          ) : null}
        </div>
      ) : null}

      {!streaming ? <TurnSummaryBar turn={turn} /> : null}
    </section>
  );
}
