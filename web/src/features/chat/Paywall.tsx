import { Coins, RotateCcw } from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router";
import { useMeta } from "@/api/meta";
import type { ApiError } from "@/api/errors";
import type { BillingStatus } from "@/api/types";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Money } from "@/components/ui/Money";
import { formatCredits } from "@/lib/money";
import { toast } from "@/lib/toast";
import { useDemoTopup } from "./api";

function num(detail: Record<string, unknown> | null | undefined, key: string): number | null {
  const v = detail?.[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export interface PaywallProps {
  open: boolean;
  onClose: () => void;
  /** The 402 `visitor_insufficient_points` that opened it (null when opened from "Top up"). */
  error: ApiError | null;
  /** The message waiting to be resent (null = top-up only). */
  pendingText: string | null;
  billing: BillingStatus | null | undefined;
  /** `dataUpdatedAt` of the billing query: a refetch newer than the top-up wins. */
  billingUpdatedAt?: number;
  onResend: (text: string) => void;
}

/**
 * Paywall for 402 `visitor_insufficient_points` (SPEC §11.4): explains the shortfall, offers the
 * demo top-up (`meta.demo_topup_cents`, never hardcoded) and a Resend of the blocked message.
 */
export function Paywall({ open, onClose, error, pendingText, billing, billingUpdatedAt = 0, onResend }: PaywallProps) {
  const meta = useMeta();
  const topup = useDemoTopup();
  const { reset } = topup;
  // Each opening starts clean: a top-up from an earlier opening must not mask a newer balance.
  useEffect(() => {
    if (open) reset();
  }, [open, reset]);
  const close = () => {
    reset();
    onClose();
  };
  const required = num(error?.detail, "required_cents") ?? billing?.next_round_charge_cents ?? billing?.price_cents ?? null;
  // The top-up response is the freshest balance until the invalidated billing status refetches.
  const topupBalance =
    topup.data && topup.submittedAt >= billingUpdatedAt ? topup.data.wallet.balance_cents : null;
  const balance = topupBalance ?? billing?.balance_cents ?? num(error?.detail, "balance_cents");
  const topupCents = meta.data?.demo_topup_cents ?? null;
  const canAfford = required != null && balance != null ? balance >= required : topup.isSuccess;

  const doTopup = () =>
    topup.mutate(undefined, {
      onSuccess: (res) => toast.success(`Added ${formatCredits(res.entry.amount_cents)} demo credits`),
    });

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) close();
      }}
      title={pendingText ? "Not enough credits for this round" : "Top up credits"}
      description="Credits are simulated for this demo. Add free demo credits to keep chatting."
      size="sm"
      footer={
        <>
          <Button variant="outline" onClick={close}>
            Close
          </Button>
          {pendingText ? (
            <Button
              disabled={!canAfford}
              icon={<RotateCcw aria-hidden className="h-4 w-4" />}
              onClick={() => {
                reset();
                onResend(pendingText);
              }}
            >
              Resend
            </Button>
          ) : null}
        </>
      }
    >
      <dl className="grid grid-cols-2 gap-3 rounded-card border border-border bg-sunken p-4 text-sm">
        <div className="min-w-0">
          <dt className="text-muted">Next round</dt>
          <dd className="mt-0.5 font-medium">{required != null ? <Money cents={required} /> : "—"}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-muted">Your balance</dt>
          <dd className="mt-0.5 font-medium" data-testid="paywall-balance">
            {balance != null ? <Money cents={balance} /> : "—"}
          </dd>
        </div>
      </dl>
      <div className="mt-4 flex flex-col gap-2">
        <Button
          variant={topup.isSuccess ? "secondary" : "primary"}
          loading={topup.isPending}
          disabled={topupCents == null}
          onClick={doTopup}
          icon={<Coins aria-hidden className="h-4 w-4" />}
        >
          {topupCents != null ? `Add ${formatCredits(topupCents)} demo credits` : "Add demo credits"}
        </Button>
        {topup.isError ? <ErrorLine error={topup.error} /> : null}
        <p className="text-center text-[13px] text-muted">
          or open your{" "}
          <Link to="/wallet" className="font-medium text-primary hover:underline" onClick={close}>
            wallet
          </Link>
        </p>
      </div>
    </Dialog>
  );
}
