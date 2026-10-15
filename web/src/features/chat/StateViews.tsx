/** Billing hint above the composer and the full-page agent states (unavailable / not found). */
import { CircleCheck, CircleSlash, Coins, Info, SearchX } from "lucide-react";
import { Link } from "react-router";
import { errorTraceId } from "@/api/errors";
import type { BillingStatus } from "@/api/types";
import { buttonClasses } from "@/components/ui/Button";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { cn } from "@/lib/cn";
import { billingHint, type PublicPricing } from "./billingHint";

export function BillingHint({
  status,
  pricing,
  signedIn = false,
  loading = false,
  error,
  onRetry,
  onTopUp,
  className,
}: {
  status: BillingStatus | null | undefined;
  pricing: PublicPricing;
  signedIn?: boolean;
  loading?: boolean;
  /** Failure of `GET /billing` for a signed-in visitor: shown with its trace id. */
  error?: unknown;
  onRetry?: () => void;
  onTopUp: () => void;
  className?: string;
}) {
  const hint = billingHint(status, pricing, { signedIn, loading });
  const failed = signedIn && !status && !loading && error != null;
  const Icon = hint.tone === "warning" ? Info : hint.tone === "success" ? CircleCheck : Coins;
  return (
    <div className={className}>
      <div
        className={cn(
          "flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[13px]",
          hint.tone === "warning" ? "text-warning" : "text-muted",
          loading && signedIn && !status && "animate-pulse",
        )}
        data-testid="billing-hint"
        aria-busy={(loading && signedIn && !status) || undefined}
      >
        <Icon aria-hidden className={cn("h-3.5 w-3.5 shrink-0", hint.tone === "success" && "text-success")} />
        <span className="min-w-0">{hint.text}</span>
        {hint.topUp ? (
          <button type="button" onClick={onTopUp} className="font-medium text-primary hover:underline">
            Top up
          </button>
        ) : null}
      </div>
      {failed ? (
        <ErrorLine
          className="mt-1 text-[13px]"
          error={error}
          message="Couldn't load your balance for this agent."
          onRetry={onRetry}
        />
      ) : null}
    </div>
  );
}

/**
 * "Temporarily unavailable" (SPEC §11.4): agent paused/deleted (410 `agent_offline`) or the
 * creator's balance is exhausted (402 `insufficient_points`). Never asks the visitor to top up.
 */
export function UnavailableState({ error, className }: { error?: unknown; className?: string }) {
  return (
    <div
      className={cn(
        "flex flex-col items-center rounded-card border border-border bg-surface px-6 py-14 text-center",
        className,
      )}
      data-testid="agent-unavailable"
    >
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-slate-100 text-muted">
        <CircleSlash aria-hidden className="h-5 w-5" />
      </div>
      <h2 className="text-lg font-semibold">This agent is temporarily unavailable</h2>
      <p className="mt-1 max-w-md text-sm text-muted">
        It is not taking conversations right now. Please check back later, or try another agent.
      </p>
      <Link to="/" className={cn(buttonClasses({ variant: "outline" }), "mt-5")}>
        Browse agents
      </Link>
      {errorTraceId(error) ? (
        <p className="mt-4 font-mono text-2xs break-all text-subtle" data-testid="trace-id">
          Trace ID: {errorTraceId(error)}
        </p>
      ) : null}
    </div>
  );
}

export function NotFoundState() {
  return (
    <div className="flex flex-col items-center rounded-card border border-border bg-surface px-6 py-14 text-center">
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-primary-soft text-primary">
        <SearchX aria-hidden className="h-5 w-5" />
      </div>
      <h1 className="text-lg font-semibold">We couldn&apos;t find this agent</h1>
      <p className="mt-1 max-w-md text-sm text-muted">The link may be mistyped, or the agent was removed.</p>
      <Link to="/" className={cn(buttonClasses({ variant: "outline" }), "mt-5")}>
        Browse agents
      </Link>
    </div>
  );
}
