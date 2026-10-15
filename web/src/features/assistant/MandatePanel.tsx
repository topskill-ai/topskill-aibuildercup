import { ArrowUpRight, ChevronDown, Lock, Receipt, ShieldCheck } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { useMeta } from "@/api/meta";
import type { Mandate } from "@/api/types";
import { Button } from "@/components/ui/Button";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Money } from "@/components/ui/Money";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { A2ACallList } from "@/features/mandates/A2ACallList";
import { BudgetBar } from "@/features/mandates/BudgetBar";
import { MandateStatusBadge, RevokeButton } from "@/features/mandates/MandateList";
import { audienceLabel, effectiveStatus, formatCountdown, useNow } from "@/features/mandates/model";
import { useRecentLedger } from "@/features/wallet/api";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";
import { formatCredits } from "@/lib/money";
import { liveMandate, type MandateCounters } from "./timeline";
import type { ActiveMandate } from "./useAssistant";

function Fact({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className={cn("min-w-0 rounded-control bg-slate-50 px-3 py-2", className)}>
      <div className="text-2xs font-medium tracking-wide text-subtle uppercase">{label}</div>
      <div className="mt-0.5 truncate text-sm font-semibold">{children}</div>
    </div>
  );
}

export interface MandateCardProps {
  active: ActiveMandate | null;
  counters: MandateCounters | null;
  streaming: boolean;
  onAuthorise: () => void;
  onRevoked: (m: Mandate) => void;
}

/** Right-panel mandate card: intent, budget bar, per-call max, expiry countdown, revoke. */
export function MandateCard({ active, counters, streaming, onAuthorise, onRevoked }: MandateCardProps) {
  const meta = useMeta();
  const now = useNow(1000);
  const [showCalls, setShowCalls] = useState(false);

  if (!active) {
    const d = meta.data?.a2a_mandate_defaults;
    return (
      <section aria-label="Payment mandate" className="rounded-card border border-dashed border-border-strong bg-surface p-5">
        <div className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary-soft text-primary">
            <ShieldCheck aria-hidden className="h-4 w-4" />
          </span>
          <h2 className="text-base font-semibold">No budget authorised</h2>
        </div>
        <p className="mt-2 text-sm text-muted">
          Before the assistant hires anyone, you sign an AP2-style intent mandate: a total budget, a per-call limit and an
          expiry. Agents verify it on every call.
        </p>
        {d ? (
          <p className="mt-2 text-[13px] text-subtle">
            Default: {formatCredits(d.budget_cents)} credits, max {formatCredits(d.max_amount_cents)} per call, {d.ttl_minutes} min.
          </p>
        ) : null}
        <Button className="mt-4 w-full" onClick={onAuthorise} icon={<Lock aria-hidden className="h-4 w-4" />}>
          Authorise budget
        </Button>
      </section>
    );
  }

  const m = liveMandate(active.mandate, counters);
  const status = active.dead === "mandate_revoked" ? "revoked" : effectiveStatus(m, now);
  const usable = status === "active" && !active.dead;
  const remainingMs = Date.parse(m.expires_at) - now;
  const left = Math.max(0, m.budget_cents - m.spent_cents);

  return (
    <section aria-label="Payment mandate" className="overflow-hidden rounded-card border border-border bg-surface">
      <div className="bg-gradient-to-br from-indigo-50 via-white to-violet-50 px-5 pt-5 pb-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-white">
              <ShieldCheck aria-hidden className="h-4 w-4" />
            </span>
            <div>
              <h2 className="text-base leading-5 font-semibold">Payment mandate</h2>
              <div className="font-mono text-2xs text-subtle">{m.id}</div>
            </div>
          </div>
          <MandateStatusBadge status={status} />
        </div>
        <p className="mt-3 line-clamp-3 text-sm text-text" title={m.intent}>
          “{m.intent}”
        </p>
        <BudgetBar className="mt-4" size="lg" spentCents={m.spent_cents} budgetCents={m.budget_cents} inactive={!usable && status !== "exhausted"} />
      </div>
      <div className="grid grid-cols-2 gap-2 px-5 py-4">
        <Fact label="Max per call">
          <Money cents={m.max_amount_cents} unit={false} />
        </Fact>
        <Fact label="Remaining">
          <Money cents={left} unit={false} />
        </Fact>
        <Fact label="Paid calls">{m.calls}</Fact>
        <Fact label={status === "expired" ? "Expired" : "Expires in"}>
          <span className={cn("font-mono tabular-nums", remainingMs < 5 * 60_000 && status === "active" && "text-warning")}>
            {status === "expired" ? "—" : formatCountdown(remainingMs)}
          </span>
        </Fact>
        <Fact label="Agents" className="col-span-2">
          {audienceLabel(m.audience)} · signed Ed25519 (EdDSA)
        </Fact>
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t border-border px-5 py-3">
        {usable ? (
          <RevokeButton mandateId={m.id} onRevoked={onRevoked} />
        ) : (
          <Button size="sm" onClick={onAuthorise} disabled={streaming} icon={<Lock aria-hidden className="h-4 w-4" />}>
            Authorise new budget
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto"
          aria-expanded={showCalls}
          onClick={() => setShowCalls((s) => !s)}
          icon={<ChevronDown aria-hidden className={cn("h-4 w-4 transition-transform", showCalls && "rotate-180")} />}
        >
          Calls
        </Button>
      </div>
      {showCalls ? (
        <div className="border-t border-border px-5 py-1">
          <A2ACallList mandateId={m.id} refetchInterval={streaming ? 3000 : false} />
        </div>
      ) : null}
    </section>
  );
}

/** Latest `a2a_payment` ledger entries (SPEC §11.4 assistant mini ledger). */
export function MiniLedger({ streaming }: { streaming: boolean }) {
  const ledger = useRecentLedger("a2a_payment", 5, { refetchInterval: streaming ? 4000 : false });
  return (
    <section aria-labelledby="mini-ledger-title" className="rounded-card border border-border bg-surface">
      <div className="flex items-center justify-between gap-2 px-5 pt-4 pb-2">
        <h2 id="mini-ledger-title" className="flex items-center gap-2 text-sm font-semibold">
          <Receipt aria-hidden className="h-4 w-4 text-subtle" />
          A2A payments
        </h2>
        <Link to="/wallet?category=a2a_payment" className="inline-flex items-center gap-0.5 text-[13px] font-medium text-primary hover:underline">
          Wallet <ArrowUpRight aria-hidden className="h-3.5 w-3.5" />
        </Link>
      </div>
      <div className="px-5 pb-4">
        {ledger.isPending ? (
          <SkeletonRows rows={2} />
        ) : ledger.isError ? (
          <ErrorLine error={ledger.error} onRetry={() => void ledger.refetch()} />
        ) : ledger.data.items.length === 0 ? (
          <p className="text-[13px] text-subtle">No A2A payments yet. Paid calls appear here the moment they settle.</p>
        ) : (
          <ul className="divide-y divide-border">
            {ledger.data.items.map((e) => (
              <li key={e.id} className="flex animate-fade-in items-center justify-between gap-3 py-2">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{e.agent_title ?? e.memo}</div>
                  <div className="truncate text-2xs text-subtle">
                    {formatRelative(e.created_at)}
                    {e.counterparty_name ? ` · to ${e.counterparty_name}` : ""}
                  </div>
                </div>
                <Money cents={e.amount_cents} unit={false} className="shrink-0 text-sm font-medium" />
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

/** Compact sticky budget strip for narrow screens, where the panel sits below the chat. */
export function MandateStrip({ active, counters }: { active: ActiveMandate | null; counters: MandateCounters | null }) {
  const now = useNow(1000);
  if (!active) return null;
  const m = liveMandate(active.mandate, counters);
  const status = active.dead === "mandate_revoked" ? "revoked" : effectiveStatus(m, now);
  return (
    <a
      href="#mandate-panel"
      className="sticky top-14 z-20 -mx-4 mb-4 flex items-center gap-3 border-b border-border bg-surface/95 px-4 py-2 backdrop-blur sm:-mx-6 sm:px-6 lg:hidden"
    >
      <ShieldCheck aria-hidden className="h-4 w-4 shrink-0 text-primary" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2 text-[13px]">
          <span className="font-medium">Mandate</span>
          <span>
            <Money cents={m.spent_cents} unit={false} /> / <Money cents={m.budget_cents} unit={false} />
          </span>
        </div>
        <BudgetBar spentCents={m.spent_cents} budgetCents={m.budget_cents} inactive={status === "revoked" || status === "expired"} />
      </div>
      <MandateStatusBadge status={status} />
    </a>
  );
}
