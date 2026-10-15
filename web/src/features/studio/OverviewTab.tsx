/** Agent detail → Overview: knowledge readiness, stats tiles, recent earnings and conversations. */
import { Activity, Bot, ClipboardList, Coins, FileText, MessagesSquare, RefreshCw } from "lucide-react";
import type { Agent } from "@/api/types";
import { isApiError } from "@/api/errors";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Money } from "@/components/ui/Money";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { Stat } from "@/components/ui/Stat";
import { formatNumber, formatRelative } from "@/lib/format";
import { knowledgeBadge } from "@/features/knowledge/status";
import { useConversations, useEarnings } from "./api";
import { IncomeList } from "./IncomeList";
import { a2aPriceLabel, priceLabel } from "./labels";

export function KnowledgeBadge({ agent, onOpen }: { agent: Agent; onOpen?: () => void }) {
  const b = knowledgeBadge(agent.knowledge);
  const badge = (
    <Badge
      variant={b.variant}
      dot
      data-testid="knowledge-badge"
      icon={b.pending ? <RefreshCw aria-hidden className="h-3 w-3 animate-spin" /> : undefined}
    >
      {b.label}
    </Badge>
  );
  return onOpen ? (
    <button type="button" onClick={onOpen} className="rounded-chip" aria-label={`${b.label}. Open the Knowledge tab`}>
      {badge}
    </button>
  ) : (
    badge
  );
}

export function OverviewTab({
  agent,
  onOpenTab,
}: {
  agent: Agent;
  onOpenTab: (tab: "knowledge" | "records" | "versions") => void;
}) {
  const s = agent.stats;
  const earnings = useEarnings(agent.id, 10);
  const conversations = useConversations(agent.id, 5);
  // The owner conversation list is a [SHOULD] endpoint; without it the card shows knowledge instead.
  const conversationsMissing = isApiError(conversations.error) && conversations.error.status === 404;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{priceLabel(agent.pricing)}</Badge>
        <Badge variant="outline">{a2aPriceLabel(agent.pricing)}</Badge>
        <Badge variant="outline">Version {agent.active_version}</Badge>
        {agent.config.web_search_enabled ? <Badge variant="outline">Google Search on</Badge> : null}
        {agent.config.safety_screening !== false ? <Badge variant="outline">Model Armor on</Badge> : null}
        {agent.config.record_schema ? (
          <Badge variant="outline">Lead capture · {agent.config.record_schema.fields.length} fields</Badge>
        ) : null}
      </div>

      <section aria-label="Stats" className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Conversations" icon={<MessagesSquare />} value={formatNumber(s.conversations)} />
        <Stat label="Rounds" icon={<Activity />} value={formatNumber(s.rounds)} hint={`${formatNumber(s.paid_rounds)} paid`} />
        <Stat label="Earned" icon={<Coins />} value={<Money cents={s.creator_earned_cents} unit={false} />} hint="credits" />
        <Stat
          label="Visitor gross"
          icon={<Coins />}
          value={<Money cents={s.visitor_gross_cents} unit={false} />}
          hint="paid by visitors"
        />
        <Stat
          label="A2A calls"
          icon={<Bot />}
          value={formatNumber(s.a2a_calls)}
          hint={<Money cents={s.a2a_gross_cents} unit={false} className="text-subtle" />}
        />
        <Stat
          label="Records"
          icon={<ClipboardList />}
          value={formatNumber(s.records)}
          hint={s.last_activity_at ? `active ${formatRelative(s.last_activity_at)}` : "no activity yet"}
        />
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Recent earnings" description="Your share of visitor rounds and A2A calls." />
          <div className="pt-2 pb-1">
            {earnings.isPending ? (
              <SkeletonRows rows={3} className="p-5" />
            ) : earnings.error ? (
              <ErrorLine className="p-5" error={earnings.error} onRetry={() => void earnings.refetch()} />
            ) : earnings.data.items.length > 0 ? (
              <IncomeList items={earnings.data.items} showAgent={false} />
            ) : (
              <p className="px-5 pb-5 text-sm text-muted">No earnings yet.</p>
            )}
          </div>
        </Card>

        {conversationsMissing ? (
          <Card>
            <CardHeader title="Knowledge" description="Documents the agent searches and cites." />
            <div className="space-y-3 p-5 text-sm">
              <p className="text-muted">
                {agent.knowledge.files === 0
                  ? "No documents yet. Add PDFs or DOCX files so answers cite your material."
                  : `${agent.knowledge.ready} of ${agent.knowledge.files} documents ready.`}
              </p>
              <Button variant="outline" size="sm" icon={<FileText className="h-4 w-4" />} onClick={() => onOpenTab("knowledge")}>
                Manage knowledge
              </Button>
            </div>
          </Card>
        ) : (
          <Card>
            <CardHeader title="Recent conversations" description="Latest visitor chats on the web." />
            <div className="pt-2 pb-1">
              {conversations.isPending ? (
                <SkeletonRows rows={3} className="p-5" />
              ) : conversations.error ? (
                <ErrorLine className="p-5" error={conversations.error} onRetry={() => void conversations.refetch()} />
              ) : conversations.data.items.length > 0 ? (
                <ul className="divide-y divide-border">
                  {conversations.data.items.map((c) => (
                    <li key={c.id} className="px-5 py-3">
                      <div className="truncate text-sm font-medium">{c.title || "Untitled conversation"}</div>
                      <div className="mt-0.5 truncate text-[13px] text-muted">
                        {c.visitor_name} · {c.rounds_ok} round{c.rounds_ok === 1 ? "" : "s"} ·{" "}
                        <span className="text-subtle">{formatRelative(c.updated_at)}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="px-5 pb-5 text-sm text-muted">No conversations yet. Share the public page to get started.</p>
              )}
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
