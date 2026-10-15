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
        "group relative flex min-w-0 flex-col overflow-hidden rounded-card border border-border bg-surface",
        "transition-colors duration-150 ease-out hover:border-border-strong focus-visible:outline-offset-2",
      )}
    >
      <span aria-hidden className={cn("h-1.5 w-full", accent.solid)} />
      <div className="flex min-w-0 flex-1 flex-col p-5">
        <div className="flex min-w-0 items-start gap-3">
          <Avatar src={agent.avatar_url} name={agent.title} accent={agent.accent_color} size={48} ring />
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-base leading-6 font-semibold group-hover:text-primary">{agent.title}</h3>
            <p className="truncate text-[13px] text-muted">
              by {agent.creator.name || "Unknown creator"}
              {agent.creator.headline ? <span className="text-subtle"> · {agent.creator.headline}</span> : null}
            </p>
          </div>
        </div>
        <p className="mt-3 line-clamp-2 min-h-[2lh] text-sm text-muted">{agent.tagline || "No tagline yet."}</p>
        <FeatureBadges features={agent.features} className="mt-3" />
        <div className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-4">
          <PriceChip pricing={agent.pricing} accent={agent.accent_color} />
          <span className="flex items-center gap-3 text-xs text-subtle">
            <span className="inline-flex items-center gap-1" title="Conversations">
              <MessageSquare aria-hidden className="h-3.5 w-3.5" />
              <span className="sr-only">Conversations:</span>
              {formatNumber(agent.stats.conversations)}
            </span>
            {agent.features.a2a ? (
              <span className="inline-flex items-center gap-1" title="A2A calls">
                <Network aria-hidden className="h-3.5 w-3.5" />
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
