import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { formatCredits } from "@/lib/money";
import { spentPercent } from "./model";

export interface BudgetBarProps {
  spentCents: number;
  budgetCents: number;
  /** Larger bar with the amounts above it (assistant mandate card). */
  size?: "sm" | "lg";
  /** Dim the bar (revoked / expired mandates). */
  inactive?: boolean;
  className?: string;
}

/**
 * Spent / budget bar. When `spentCents` grows, the fill animates and a "+X.XX" pulse appears so a
 * paid call is visible at a glance; a rejected call leaves the bar untouched.
 */
export function BudgetBar({ spentCents, budgetCents, size = "sm", inactive, className }: BudgetBarProps) {
  const pct = spentPercent(spentCents, budgetCents);
  const prev = useRef(spentCents);
  const [delta, setDelta] = useState<{ cents: number; key: number } | null>(null);

  useEffect(() => {
    const diff = spentCents - prev.current;
    prev.current = spentCents;
    if (diff <= 0) return;
    setDelta({ cents: diff, key: Date.now() });
    const id = window.setTimeout(() => setDelta(null), 2400);
    return () => window.clearTimeout(id);
  }, [spentCents]);

  const tone = pct >= 90 ? "bg-danger" : pct >= 70 ? "bg-warning" : "bg-primary";

  return (
    <div className={cn("min-w-0", className)}>
      {size === "lg" ? (
        <div className="mb-2 flex items-end justify-between gap-3">
          <div className="flex items-baseline gap-1.5">
            <span className="font-mono text-3xl font-semibold tracking-tight tabular-nums" data-testid="mandate-spent">
              {formatCredits(spentCents)}
            </span>
            <span className="text-sm text-muted">
              / <span className="font-mono tabular-nums">{formatCredits(budgetCents)}</span> credits
            </span>
          </div>
          {delta ? (
            <span key={delta.key} className="animate-fade-in rounded-chip bg-primary-soft px-2 py-0.5 font-mono text-xs font-semibold text-primary">
              +{formatCredits(delta.cents)}
            </span>
          ) : null}
        </div>
      ) : null}
      <div
        role="progressbar"
        aria-label="Budget spent"
        aria-valuemin={0}
        aria-valuemax={budgetCents}
        aria-valuenow={spentCents}
        aria-valuetext={`${formatCredits(spentCents)} of ${formatCredits(budgetCents)} credits spent`}
        className={cn("w-full overflow-hidden rounded-chip bg-mist", size === "lg" ? "h-3" : "h-1.5")}
      >
        <div
          className={cn(
            "h-full rounded-chip transition-[width] duration-700 ease-out",
            inactive ? "bg-border-strong" : tone,
            delta && !inactive && "ring-3 ring-primary/20",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
