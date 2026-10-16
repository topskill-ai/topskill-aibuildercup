import { MessageSquare, Network } from "lucide-react";
import { Link } from "react-router";
import type { PublicAgentCard } from "@/api/types";
import { Avatar } from "@/components/ui/Avatar";
import { accentClasses } from "@/lib/accent";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import { FeatureBadges, PriceChip } from "./AgentBits";

/** Marketplace tile (SPEC §11.4): avatar, title, tagline, creator, price chip, feature badges. */
export function AgentCard({ agent }: { agent: PublicAgentCard }) {
  const accent = accentClasses(agent.accent_color);
  return (
    <Link
      to={`/a/${encodeURIComponent(agent.slug)}`}
      className={cn(
        "group relative flex min-w-0 flex-col rounded-card border border-border bg-surface px-6 pt-6 pb-5",
        "transition-[translate,box-shadow,border-color] duration-200 ease-out hover:-translate-y-1 hover:border-border-strong hover:shadow-lift",
        "focus-visible:outline-offset-2",
      )}
    >
      {/* Accent tab hanging from the top edge. */}
      <span aria-hidden className={cn("absolute -top-px right-[22px] left-[22px] h-[3px] rounded-b-[4px]", accent.solid)} />
      <div className="flex min-w-0 items-center gap-3.5">
        <Avatar src={agent.avatar_url} name={agent.title} accent={agent.accent_color} size={50} solid />
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-base leading-snug font-semibold group-hover:text-primary">{agent.title}</h3>
          <p className="mt-0.5 truncate text-[12.5px] text-subtle">
            by <b className="font-semibold text-muted">{agent.creator.name || "Unknown creator"}</b>
            {agent.creator.headline ? <span> · {agent.creator.headline}</span> : null}
          </p>
        </div>
      </div>
      <p className="mt-4 line-clamp-2 min-h-[2lh] text-[13.5px] leading-relaxed text-muted">
        {agent.tagline || "No tagline yet."}
      </p>
      <FeatureBadges features={agent.features} className="mt-3.5" />
      <div className="mt-auto pt-4">
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-mist pt-4">
          <PriceChip pricing={agent.pricing} accent={agent.accent_color} tone="money" />
          <span className="flex items-center gap-3.5 text-[12.5px] font-medium text-subtle tabular-nums">
            <span className="inline-flex items-center gap-1" title="Conversations">
              <MessageSquare aria-hidden className="h-[13px] w-[13px]" />
              <span className="sr-only">Conversations:</span>
              {formatNumber(agent.stats.conversations)}
            </span>
            {agent.features.a2a ? (
              <span className="inline-flex items-center gap-1" title="A2A calls">
                <Network aria-hidden className="h-[13px] w-[13px]" />
                <span className="sr-only">A2A calls:</span>
                {formatNumber(agent.stats.a2a_calls)}
              </span>
            ) : null}
          </span>
        </div>
      </div>
    </Link>
  );
}
