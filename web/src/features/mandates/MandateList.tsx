import { ChevronDown, ShieldCheck, ShieldOff } from "lucide-react";
import { useState } from "react";
import type { Mandate } from "@/api/types";
import { Badge, type BadgeVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Money } from "@/components/ui/Money";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { cn } from "@/lib/cn";
import { formatDateTime, plural } from "@/lib/format";
import { toast, toastApiError } from "@/lib/toast";
import { A2ACallList } from "./A2ACallList";
import { isMissingEndpoint, useMandates, useRevokeMandate } from "./api";
import { BudgetBar } from "./BudgetBar";
import { audienceLabel, effectiveStatus, formatCountdown, useNow, type EffectiveMandateStatus } from "./model";

const STATUS_BADGE: Record<EffectiveMandateStatus, { label: string; variant: BadgeVariant }> = {
  active: { label: "Active", variant: "success" },
  exhausted: { label: "Budget used", variant: "warning" },
  expired: { label: "Expired", variant: "neutral" },
  revoked: { label: "Revoked", variant: "danger" },
};

export function MandateStatusBadge({ status }: { status: EffectiveMandateStatus }) {
  const s = STATUS_BADGE[status];
  return (
    <Badge variant={s.variant} dot>
      {s.label}
    </Badge>
  );
}

/** Revoke button with an inline confirm step. */
export function RevokeButton({ mandateId, onRevoked, size = "sm" }: { mandateId: string; onRevoked?: (m: Mandate) => void; size?: "sm" | "md" }) {
  const revoke = useRevokeMandate();
  const [confirming, setConfirming] = useState(false);
  if (!confirming)
    return (
      <Button variant="outline" size={size} icon={<ShieldOff aria-hidden className="h-4 w-4" />} onClick={() => setConfirming(true)}>
        Revoke
      </Button>
    );
  return (
    <span className="flex items-center gap-2">
      <Button variant="ghost" size={size} onClick={() => setConfirming(false)} disabled={revoke.isPending}>
        Keep
      </Button>
      <Button
        variant="danger"
        size={size}
        loading={revoke.isPending}
        onClick={() =>
          revoke.mutate(mandateId, {
            onSuccess: (m) => {
              setConfirming(false);
              toast.success("Mandate revoked. Agents can no longer charge it.");
              onRevoked?.(m);
            },
            onError: (err) => toastApiError(err),
          })
        }
      >
        Confirm revoke
      </Button>
    </span>
  );
}

function MandateRow({ mandate, now }: { mandate: Mandate; now: number }) {
  const [open, setOpen] = useState(false);
  const status = effectiveStatus(mandate, now);
  const remaining = Date.parse(mandate.expires_at) - now;
  return (
    <li className="border-b border-border last:border-b-0">
      <div className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:gap-6">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <MandateStatusBadge status={status} />
            <span className="truncate font-medium" title={mandate.intent}>
              {mandate.intent}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[13px] text-muted">
            <span>{audienceLabel(mandate.audience)}</span>
            <span>
              Max <Money cents={mandate.max_amount_cents} unit={false} /> per call
            </span>
            <span>{plural(mandate.calls, "paid call")}</span>
            <span>
              {status === "active" ? `Expires in ${formatCountdown(remaining)}` : `Issued ${formatDateTime(mandate.issued_at)}`}
            </span>
          </div>
          <div className="mt-1 font-mono text-2xs text-subtle">{mandate.id}</div>
        </div>
        <div className="w-full sm:w-56">
          <div className="mb-1 flex justify-between text-[13px]">
            <span className="text-muted">Spent</span>
            <span>
              <Money cents={mandate.spent_cents} unit={false} /> / <Money cents={mandate.budget_cents} unit={false} />
            </span>
          </div>
          <BudgetBar spentCents={mandate.spent_cents} budgetCents={mandate.budget_cents} inactive={status === "revoked" || status === "expired"} />
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {status === "active" || status === "exhausted" ? <RevokeButton mandateId={mandate.id} /> : null}
          <Button
            variant="ghost"
            size="sm"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
            icon={<ChevronDown aria-hidden className={cn("h-4 w-4 transition-transform", open && "rotate-180")} />}
          >
            Calls
          </Button>
        </div>
      </div>
      {open ? (
        <div className="border-t border-border bg-sunken px-5 py-2">
          <A2ACallList mandateId={mandate.id} />
        </div>
      ) : null}
    </li>
  );
}

/**
 * Payment mandates of the signed-in user ([SHOULD]): status, budget bar, calls, revoke. Returns
 * null when the backend has no mandate list endpoint.
 */
export function MandateList() {
  const mandates = useMandates();
  const now = useNow(1000);
  const items = mandates.data?.pages.flatMap((p) => p.items) ?? [];

  if (mandates.isError && isMissingEndpoint(mandates.error)) return null;

  return (
    <section aria-labelledby="mandates-title" className="rounded-card border border-border bg-surface">
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 pt-5 pb-4">
        <div>
          <h2 id="mandates-title" className="flex items-center gap-2 text-base font-semibold">
            <ShieldCheck aria-hidden className="h-4 w-4 text-primary" />
            Payment mandates
          </h2>
          <p className="mt-0.5 text-sm text-muted">
            Signed spending envelopes you authorised for agents. Each A2A call is checked against them; rejected calls are never charged.
          </p>
        </div>
      </div>
      <div className="border-t border-border">
        {mandates.isPending ? (
          <SkeletonRows rows={2} className="p-5" />
        ) : mandates.isError ? (
          <div className="p-5">
            <ErrorLine error={mandates.error} onRetry={() => void mandates.refetch()} />
          </div>
        ) : items.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={<ShieldCheck />}
              title="No mandates yet"
              description="Authorise a budget on the Enterprise Assistant page to let it hire agents for you."
            />
          </div>
        ) : (
          <ul aria-label="Mandates">
            {items.map((m) => (
              <MandateRow key={m.id} mandate={m} now={now} />
            ))}
          </ul>
        )}
        {mandates.hasNextPage ? (
          <div className="flex justify-center border-t border-border p-3">
            <Button variant="outline" size="sm" loading={mandates.isFetchingNextPage} onClick={() => void mandates.fetchNextPage()}>
              Load more
            </Button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
