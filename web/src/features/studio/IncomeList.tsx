/** Income ledger rows (dashboard "Recent income", agent Overview "Recent earnings"). */
import { Bot, Globe2 } from "lucide-react";
import type { LedgerEntry } from "@/api/types";
import { Badge } from "@/components/ui/Badge";
import { Money } from "@/components/ui/Money";
import { cn } from "@/lib/cn";
import { formatDateTime, formatRelative } from "@/lib/format";
import { incomeChannel } from "./income";

export function IncomeList({
  items,
  freshIds,
  showAgent = true,
  className,
}: {
  items: LedgerEntry[];
  /** Ids that just arrived (highlighted briefly). */
  freshIds?: ReadonlySet<string>;
  showAgent?: boolean;
  className?: string;
}) {
  return (
    <ul className={cn("divide-y divide-border", className)}>
      {items.map((e) => {
        const channel = incomeChannel(e.category);
        return (
          <li
            key={e.id}
            data-testid="income-row"
            className={cn(
              "flex items-start gap-3 px-5 py-3 transition-colors duration-700",
              freshIds?.has(e.id) && "animate-fade-in bg-success-soft",
            )}
          >
            <span
              className={cn(
                "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
                channel === "a2a" ? "bg-accent-violet-soft text-accent-violet-text" : "bg-primary-soft text-primary",
              )}
              aria-hidden
            >
              {channel === "a2a" ? <Bot className="h-4 w-4" /> : <Globe2 className="h-4 w-4" />}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="min-w-0 break-words text-sm font-medium">{e.memo || e.agent_title || "Income"}</span>
                <Badge variant={channel === "a2a" ? "outline" : "neutral"}>{channel === "a2a" ? "A2A call" : "Visitor round"}</Badge>
              </div>
              <div className="mt-0.5 break-words text-[13px] text-muted">
                {[
                  showAgent && e.agent_title && !e.memo.includes(e.agent_title) ? e.agent_title : null,
                  channel === "a2a" && e.counterparty_name ? `paid by ${e.counterparty_name}` : null,
                ]
                  .filter(Boolean)
                  .map((t) => `${t} · `)
                  .join("")}
                <span className="text-subtle" title={formatDateTime(e.created_at)}>
                  {formatRelative(e.created_at)}
                </span>
              </div>
            </div>
            <Money cents={e.amount_cents} signed colorPositive className="shrink-0 text-sm font-medium" />
          </li>
        );
      })}
    </ul>
  );
}
