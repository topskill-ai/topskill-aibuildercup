import { Coins, Plus, Sparkles } from "lucide-react";
import { useMeta } from "@/api/meta";
import { Button } from "@/components/ui/Button";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatCredits } from "@/lib/money";
import { toast, toastApiError } from "@/lib/toast";
import { useDemoTopup, useWallet } from "./api";

/** Balance hero with the demo top-up action (SPEC §11.4 Wallet). */
export function BalanceCard() {
  const wallet = useWallet();
  const meta = useMeta();
  const topup = useDemoTopup();
  const topupCents = meta.data?.demo_topup_cents;

  const onTopup = () =>
    topup.mutate(undefined, {
      onSuccess: (res) => toast.success(`Added ${formatCredits(res.entry.amount_cents)} demo credits`),
      onError: (err) => toastApiError(err),
    });

  return (
    <section
      aria-label="Balance"
      className="relative overflow-clip rounded-card bg-gradient-to-br from-brand-from via-brand-from to-brand-to p-6 text-primary-fg sm:p-7"
    >
      <div aria-hidden className="pointer-events-none absolute -right-16 -top-20 h-64 w-64 rounded-full bg-primary-fg/10 blur-2xl" />
      <div aria-hidden className="pointer-events-none absolute -bottom-24 left-1/3 h-56 w-56 rounded-full bg-accent-violet/20 blur-2xl" />
      <div className="relative flex flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium text-primary-fg">
            <Coins aria-hidden className="h-4 w-4" />
            Available balance
          </div>
          <div className="mt-2 flex items-baseline gap-2" data-testid="wallet-balance">
            {wallet.isPending ? (
              <Skeleton className="h-11 w-48 bg-primary-fg/20 [background-image:none]" />
            ) : wallet.isError ? (
              <span className="text-2xl font-semibold">—</span>
            ) : (
              <>
                <span className="font-mono text-4xl font-semibold tracking-tight tabular-nums sm:text-5xl">
                  {formatCredits(wallet.data.balance_cents)}
                </span>
                <span className="text-base font-medium text-primary-fg">credits</span>
              </>
            )}
          </div>
          <p className="mt-2 text-[13px] text-primary-fg">
            Simulated credits for the demo. Agents charge per round; A2A calls are paid from budgets you authorise.
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-start gap-1.5 sm:items-end">
          <Button
            onClick={onTopup}
            loading={topup.isPending}
            disabled={topupCents === undefined}
            variant="inverse"
            size="lg"
            icon={<Plus aria-hidden className="h-4 w-4" />}
            data-testid="demo-topup"
          >
            {topupCents === undefined ? "Add demo credits" : `Add ${formatCredits(topupCents)} demo credits`}
          </Button>
          {meta.data ? (
            <span className="flex items-center gap-1 text-xs text-primary-fg">
              <Sparkles aria-hidden className="h-3 w-3" />
              Up to {meta.data.demo_topup_daily_limit} top-ups per day
            </span>
          ) : null}
        </div>
      </div>
      {wallet.isError ? (
        <div className="relative mt-4 rounded-control bg-surface p-3">
          <ErrorLine error={wallet.error} onRetry={() => void wallet.refetch()} />
        </div>
      ) : null}
    </section>
  );
}
