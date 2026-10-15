/** Creator's agents (dashboard). A table from 640px up, stacked cards below (SPEC §11.3). */
import { ChevronRight } from "lucide-react";
import { Link } from "react-router";
import type { AgentSummary } from "@/api/types";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { Money } from "@/components/ui/Money";
import { formatNumber } from "@/lib/format";
import { priceLabel } from "./labels";

function StatusBadges({ a }: { a: AgentSummary }) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      <Badge dot variant={a.status === "active" ? "success" : "neutral"}>
        {a.status === "active" ? "Active" : "Paused"}
      </Badge>
      {a.config_dirty ? <Badge variant="warning">Update pending</Badge> : null}
      {!a.listed ? <Badge variant="outline">Unlisted</Badge> : null}
    </span>
  );
}

export function AgentsTable({ agents }: { agents: AgentSummary[] }) {
  return (
    <>
      <div className="hidden overflow-x-auto sm:block">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr className="border-y border-border bg-slate-50 text-left text-xs font-medium text-muted">
              <th className="px-5 py-2.5 font-medium">Agent</th>
              <th className="px-3 py-2.5 font-medium">Status</th>
              <th className="px-3 py-2.5 font-medium">Price</th>
              <th className="px-3 py-2.5 text-right font-medium">Rounds</th>
              <th className="px-3 py-2.5 text-right font-medium">A2A calls</th>
              <th className="px-5 py-2.5 text-right font-medium">Earned</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {agents.map((a) => (
              <tr key={a.id} className="group hover:bg-slate-50">
                <td className="px-5 py-3">
                  <Link to={`/studio/agents/${a.id}`} className="flex min-w-0 items-center gap-3">
                    <Avatar src={a.avatar_url} name={a.title} accent={a.accent_color} size={32} />
                    <span className="min-w-0">
                      <span className="block truncate font-medium group-hover:text-primary">{a.title}</span>
                      <span className="block truncate font-mono text-xs text-subtle">/a/{a.slug}</span>
                    </span>
                  </Link>
                </td>
                <td className="px-3 py-3">
                  <StatusBadges a={a} />
                </td>
                <td className="px-3 py-3 whitespace-nowrap text-muted">{priceLabel(a.pricing)}</td>
                <td className="px-3 py-3 text-right font-mono tabular-nums">
                  {formatNumber(a.stats.rounds)}
                  <span className="text-subtle"> / {formatNumber(a.stats.paid_rounds)} paid</span>
                </td>
                <td className="px-3 py-3 text-right font-mono tabular-nums">{formatNumber(a.stats.a2a_calls)}</td>
                <td className="px-5 py-3 text-right">
                  <Money cents={a.stats.creator_earned_cents} unit={false} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="divide-y divide-border border-t border-border sm:hidden">
        {agents.map((a) => (
          <li key={a.id}>
            <Link to={`/studio/agents/${a.id}`} className="flex items-center gap-3 px-5 py-3">
              <Avatar src={a.avatar_url} name={a.title} accent={a.accent_color} size={36} />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{a.title}</span>
                <span className="mt-1 block">
                  <StatusBadges a={a} />
                </span>
                <span className="mt-1 block text-[13px] text-muted">
                  {priceLabel(a.pricing)} · {formatNumber(a.stats.rounds)} rounds · {formatNumber(a.stats.a2a_calls)} A2A ·{" "}
                  <Money cents={a.stats.creator_earned_cents} unit={false} /> earned
                </span>
              </span>
              <ChevronRight aria-hidden className="h-4 w-4 shrink-0 text-subtle" />
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
