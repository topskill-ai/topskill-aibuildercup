import { MessageSquare, Network } from "lucide-react";
import type { ReactNode } from "react";
import type { PublicAgent } from "@/api/types";
import { Markdown } from "@/components/Markdown";
import { Avatar } from "@/components/ui/Avatar";
import { CopyField } from "@/components/ui/CopyField";
import { Money } from "@/components/ui/Money";
import { cn } from "@/lib/cn";
import { plural } from "@/lib/format";
import { CapabilityIcon, FeatureBadges } from "../marketplace/AgentBits";
import { priceSentence } from "../marketplace/pricing";

function Section({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={cn("min-w-0 rounded-card border border-border bg-surface p-5", className)}>
      <h2 className="mb-3 text-xs font-semibold tracking-wide text-subtle uppercase">{title}</h2>
      {children}
    </section>
  );
}

/**
 * Right-hand info panel of the agent page (SPEC §11.4): creator card, pricing, capabilities and
 * the "Use via A2A" card with a copyable Agent Card URL.
 */
export function AgentInfoPanel({ agent }: { agent: PublicAgent }) {
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Section title="Creator">
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={agent.creator.name || "Creator"} accent={agent.accent_color} size={40} />
          <div className="min-w-0">
            <div className="truncate font-semibold">{agent.creator.name || "Unknown creator"}</div>
            {agent.creator.headline ? <div className="text-sm text-muted">{agent.creator.headline}</div> : null}
          </div>
        </div>
      </Section>

      <Section title="Pricing">
        <p className="text-sm text-text">{priceSentence(agent.pricing)}</p>
        <p className="mt-1 text-[13px] text-muted">Failed or stopped answers are never charged.</p>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-subtle">
          <span className="inline-flex items-center gap-1">
            <MessageSquare aria-hidden className="h-3.5 w-3.5" />
            {plural(agent.stats.conversations, "conversation")}
          </span>
          {agent.a2a.enabled ? (
            <span className="inline-flex items-center gap-1">
              <Network aria-hidden className="h-3.5 w-3.5" />
              {plural(agent.stats.a2a_calls, "A2A call")}
            </span>
          ) : null}
        </div>
        <FeatureBadges features={agent.features} className="mt-3" />
      </Section>

      {agent.capabilities.length > 0 ? (
        <Section title="Capabilities">
          <ul className="space-y-3">
            {agent.capabilities.map((c, i) => (
              <li key={`${c.name}-${i}`} className="flex min-w-0 gap-3">
                <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary-soft text-primary">
                  <CapabilityIcon name={c.icon} className="h-3.5 w-3.5" />
                </span>
                <div className="min-w-0">
                  <div className="text-sm font-medium">{c.name}</div>
                  {c.description ? <div className="text-[13px] text-muted">{c.description}</div> : null}
                </div>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {agent.a2a.enabled ? (
        <Section title="Use via A2A">
          <p className="text-sm text-muted">
            Other AI agents can call this agent over the A2A protocol with a signed payment mandate,{" "}
            <Money cents={agent.a2a.price_cents} className="font-medium text-text" /> per call.
          </p>
          <CopyField className="mt-3" label="Agent Card URL" value={agent.a2a.card_url} />
          <CopyField className="mt-3" label="Endpoint" value={agent.a2a.endpoint} />
        </Section>
      ) : null}
    </div>
  );
}

/**
 * Cold start (no messages yet): greeting, short description, up to 4 capabilities and the starter
 * questions, which send their prompt immediately (SPEC §11.4).
 */
export function ColdStart({
  agent,
  onStarter,
  disabled,
}: {
  agent: PublicAgent;
  onStarter: (prompt: string) => void;
  disabled?: boolean;
}) {
  const capabilities = agent.capabilities.slice(0, 4);
  const starters = agent.starter_questions.slice(0, 3);
  return (
    <div className="min-w-0" data-testid="cold-start">
      <div className="flex min-w-0 gap-3">
        <Avatar src={agent.avatar_url} name={agent.title} accent={agent.accent_color} size={36} />
        <div className="min-w-0 flex-1 rounded-card rounded-tl-[4px] bg-slate-50 px-4 py-3">
          <Markdown text={agent.greeting || `Hi, I'm ${agent.title}. How can I help?`} />
        </div>
      </div>
      {agent.description ? (
        <p className="mt-4 text-[13px] leading-relaxed whitespace-pre-line text-muted">{agent.description}</p>
      ) : null}
      {capabilities.length > 0 ? (
        <ul className="mt-5 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {capabilities.map((c, i) => (
            <li key={`${c.name}-${i}`} className="flex min-w-0 items-start gap-2.5 rounded-control border border-border p-3">
              <CapabilityIcon name={c.icon} className="mt-0.5 shrink-0 text-primary" />
              <div className="min-w-0">
                <div className="text-sm font-medium">{c.name}</div>
                {c.description ? <div className="text-[13px] text-muted">{c.description}</div> : null}
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      {starters.length > 0 ? (
        <div className="mt-5">
          <div className="mb-2 text-xs font-semibold tracking-wide text-subtle uppercase">Try asking</div>
          <div className="flex flex-col gap-2">
            {starters.map((s, i) => (
              <button
                key={`${s.label}-${i}`}
                type="button"
                disabled={disabled}
                onClick={() => onStarter(s.prompt || s.label)}
                title={s.prompt}
                className="min-w-0 rounded-control border border-border bg-surface px-4 py-2.5 text-left text-sm font-medium text-text transition-colors duration-150 hover:border-primary hover:bg-primary-soft hover:text-primary disabled:opacity-50"
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
