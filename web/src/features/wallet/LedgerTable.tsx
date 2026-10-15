import { ArrowDownLeft, ArrowUpRight, Receipt } from "lucide-react";
import { Link } from "react-router";
import type { LedgerCategory, LedgerEntry } from "@/api/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Money } from "@/components/ui/Money";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { cn } from "@/lib/cn";
import { formatDateTime, formatRelative } from "@/lib/format";
import { useLedger } from "./api";
import { CATEGORY_FILTERS, categoryMeta, counterpartyLine } from "./categories";

export interface CategoryChipsProps {
  value: LedgerCategory | null;
  onChange: (value: LedgerCategory | null) => void;
}

/** Category filter chips ("All" + every ledger category). */
export function CategoryChips({ value, onChange }: CategoryChipsProps) {
  const chip = (active: boolean) =>
    cn(
      "inline-flex h-8 items-center rounded-chip border px-3 text-[13px] font-medium transition-colors duration-150",
      active
        ? "border-primary bg-primary text-primary-fg"
        : "border-border bg-surface text-muted hover:border-border-strong hover:text-text",
    );
  return (
    <div role="group" aria-label="Filter by category" className="flex flex-wrap gap-2">
      <button type="button" aria-pressed={value === null} className={chip(value === null)} onClick={() => onChange(null)}>
        All
      </button>
      {CATEGORY_FILTERS.map((c) => (
        <button key={c} type="button" aria-pressed={value === c} className={chip(value === c)} onClick={() => onChange(c)}>
          {categoryMeta(c).chip}
        </button>
      ))}
    </div>
  );
}

function Direction({ cents }: { cents: number }) {
  const credit = cents > 0;
  return (
    <span
      aria-hidden
      className={cn(
        "flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
        credit ? "bg-success-soft text-success" : "bg-slate-100 text-muted",
      )}
    >
      {credit ? <ArrowDownLeft className="h-4 w-4" /> : <ArrowUpRight className="h-4 w-4" />}
    </span>
  );
}

function Description({ entry }: { entry: LedgerEntry }) {
  const sub = counterpartyLine(entry);
  return (
    <div className="min-w-0">
      <div className="truncate font-medium text-text">{entry.memo || categoryMeta(entry.category).label}</div>
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 text-[13px] text-muted">
        {sub ? <span className="truncate">{sub}</span> : null}
        {entry.agent_slug && entry.agent_title ? (
          <Link to={`/a/${entry.agent_slug}`} className="truncate text-primary hover:underline">
            {entry.agent_title}
          </Link>
        ) : null}
        {entry.mandate_id ? (
          <span className="truncate font-mono text-2xs text-subtle" title="Mandate">
            {entry.mandate_id}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function EntryRow({ entry }: { entry: LedgerEntry }) {
  const meta = categoryMeta(entry.category);
  return (
    <li className="animate-fade-in border-b border-border last:border-b-0">
      {/* ≥640px: table-like grid; below: stacked card */}
      <div className="hidden grid-cols-[minmax(0,1fr)_150px_130px_130px] items-center gap-4 px-5 py-3 sm:grid">
        <div className="flex min-w-0 items-center gap-3">
          <Direction cents={entry.amount_cents} />
          <Description entry={entry} />
        </div>
        <div className="min-w-0">
          <Badge variant={meta.badge}>{meta.label}</Badge>
          <div className="mt-0.5 text-2xs text-subtle" title={formatDateTime(entry.created_at)}>
            {formatRelative(entry.created_at)}
          </div>
        </div>
        <div className="text-right font-medium">
          <Money cents={entry.amount_cents} signed colorPositive unit={false} />
        </div>
        <div className="text-right text-[13px] text-muted">
          <Money cents={entry.balance_after_cents} unit={false} />
        </div>
      </div>
      <div className="flex items-start gap-3 px-4 py-3 sm:hidden">
        <Direction cents={entry.amount_cents} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <Description entry={entry} />
            <Money cents={entry.amount_cents} signed colorPositive unit={false} className="shrink-0 font-medium" />
          </div>
          <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2 text-2xs text-subtle">
            <span className="flex items-center gap-2">
              <Badge variant={meta.badge}>{meta.label}</Badge>
              {formatRelative(entry.created_at)}
            </span>
            <span>
              Balance <Money cents={entry.balance_after_cents} unit={false} />
            </span>
          </div>
        </div>
      </div>
    </li>
  );
}

export interface LedgerTableProps {
  category: LedgerCategory | null;
}

/** Wallet ledger, newest first, with "Load more" pagination. */
export function LedgerTable({ category }: LedgerTableProps) {
  const ledger = useLedger({ category });
  const items = ledger.data?.pages.flatMap((p) => p.items) ?? [];

  if (ledger.isPending) return <SkeletonRows rows={5} className="p-5" />;
  if (ledger.isError)
    return (
      <div className="p-5">
        <ErrorLine error={ledger.error} onRetry={() => void ledger.refetch()} />
      </div>
    );
  if (items.length === 0)
    return (
      <div className="p-5">
        <EmptyState
          icon={<Receipt />}
          title={category ? `No ${categoryMeta(category).chip.toLowerCase()} yet` : "No transactions yet"}
          description="Charges, income and top-ups appear here as they happen."
        />
      </div>
    );

  return (
    <div>
      <div className="hidden grid-cols-[minmax(0,1fr)_150px_130px_130px] gap-4 border-b border-border bg-slate-50 px-5 py-2 text-xs font-semibold tracking-wide text-subtle uppercase sm:grid">
        <span>Transaction</span>
        <span>Category</span>
        <span className="text-right">Amount</span>
        <span className="text-right">Balance after</span>
      </div>
      <ul aria-label="Ledger entries">
        {items.map((e) => (
          <EntryRow key={e.id} entry={e} />
        ))}
      </ul>
      {ledger.hasNextPage ? (
        <div className="flex justify-center border-t border-border p-3">
          <Button variant="outline" size="sm" loading={ledger.isFetchingNextPage} onClick={() => void ledger.fetchNextPage()}>
            Load more
          </Button>
        </div>
      ) : null}
    </div>
  );
}
