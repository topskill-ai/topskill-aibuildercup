import { ChevronDown, Info } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { ErrorCodes, hasCode } from "@/api/errors";
import type { PublicAgent } from "@/api/types";
import { PageBody } from "@/components/layout/PageHeader";
import { Avatar } from "@/components/ui/Avatar";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Skeleton, SkeletonText } from "@/components/ui/Skeleton";
import { AgentChat } from "@/features/chat/AgentChat";
import { AgentInfoPanel } from "@/features/chat/AgentInfo";
import { usePublicAgent } from "@/features/chat/api";
import { NotFoundState, UnavailableState } from "@/features/chat/StateViews";
import { PriceChip } from "@/features/marketplace/AgentBits";
import { ModelArmorBadge } from "@/features/guard/GuardBits";
import { accentClasses } from "@/lib/accent";
import { cn } from "@/lib/cn";

function AgentHeader({ agent }: { agent: PublicAgent }) {
  const accent = accentClasses(agent.accent_color);
  return (
    <div className={cn("border-b border-border", accent.soft)}>
      <div className="page-container flex min-w-0 items-center gap-4 py-5 sm:py-7">
        <Avatar src={agent.avatar_url} name={agent.title} accent={agent.accent_color} size={56} ring />
        <div className="min-w-0 flex-1">
          <h1 className="text-xl leading-tight font-semibold tracking-display break-words sm:text-[28px]">
            {agent.title}
          </h1>
          {agent.tagline ? <p className="mt-1 text-sm text-muted sm:text-[15px]">{agent.tagline}</p> : null}
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[13px] text-muted">
            <span className="min-w-0 truncate">by {agent.creator.name || "Unknown creator"}</span>
            <PriceChip pricing={agent.pricing} accent={agent.accent_color} />
            {agent.features.safety_screening ? <ModelArmorBadge /> : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function AgentView({ agent }: { agent: PublicAgent }) {
  const [infoOpen, setInfoOpen] = useState(false);

  useEffect(() => {
    const previous = document.title;
    document.title = `${agent.title} · TopSkill`;
    return () => {
      document.title = previous;
    };
  }, [agent.title]);

  return (
    <>
      <AgentHeader agent={agent} />
      <PageBody className="pt-4 sm:pt-6">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:gap-6">
          <aside className="order-first min-w-0 lg:order-none lg:col-start-2 lg:row-start-1" aria-label="About this agent">
            <button
              type="button"
              aria-expanded={infoOpen}
              aria-controls="agent-info"
              onClick={() => setInfoOpen((o) => !o)}
              className="flex w-full items-center justify-between gap-2 rounded-card border border-border bg-surface px-4 py-3 text-sm font-medium lg:hidden"
            >
              <span className="flex items-center gap-2">
                <Info aria-hidden className="h-4 w-4 text-subtle" />
                About this agent
              </span>
              <ChevronDown aria-hidden className={cn("h-4 w-4 transition-transform", infoOpen && "rotate-180")} />
            </button>
            <div id="agent-info" className={cn("mt-3 lg:sticky lg:top-20 lg:mt-0 lg:block", infoOpen ? "block" : "hidden")}>
              <AgentInfoPanel agent={agent} />
            </div>
          </aside>
          <div className="min-w-0 lg:col-start-1 lg:row-start-1">
            <AgentChat agent={agent} />
          </div>
        </div>
      </PageBody>
    </>
  );
}

function AgentPageSkeleton() {
  return (
    <div role="status" aria-label="Loading agent">
      <div className="border-b border-border bg-slate-50">
        <div className="page-container flex items-center gap-4 py-7">
          <Skeleton className="h-14 w-14 rounded-full" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-6 w-56 max-w-full" />
            <Skeleton className="h-4 w-80 max-w-full" />
          </div>
        </div>
      </div>
      <PageBody>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <div className="rounded-card border border-border bg-surface p-6">
            <SkeletonText lines={5} />
          </div>
          <div className="hidden rounded-card border border-border bg-surface p-6 lg:block">
            <SkeletonText lines={4} />
          </div>
        </div>
      </PageBody>
    </div>
  );
}

/** `/a/:slug` — agent profile + chat (SPEC §11.2, §11.4). Public; chatting opens the login dialog. */
export default function AgentPage() {
  const { slug = "" } = useParams();
  const query = usePublicAgent(slug);

  if (query.isPending) return <AgentPageSkeleton />;
  const err = query.error;
  // A failed background refetch (focus, network blip) keeps the page and its running chat; only
  // definitive answers (offline, not found) or a first load without data take over the page.
  const definitive = hasCode(err, ErrorCodes.agent_offline, ErrorCodes.agent_not_found, ErrorCodes.not_found);
  const agent = query.data;
  if (!agent || (query.isError && definitive)) {
    return (
      <PageBody>
        {hasCode(err, ErrorCodes.agent_offline) ? (
          <UnavailableState error={err} />
        ) : hasCode(err, ErrorCodes.agent_not_found, ErrorCodes.not_found) ? (
          <NotFoundState />
        ) : (
          <div className="rounded-card border border-border bg-surface p-5">
            <ErrorLine error={err} onRetry={() => void query.refetch()} />
            <Link to="/" className="mt-3 inline-block text-sm font-medium text-primary hover:underline">
              Back to the marketplace
            </Link>
          </div>
        )}
      </PageBody>
    );
  }
  return <AgentView key={slug} agent={agent} />;
}
