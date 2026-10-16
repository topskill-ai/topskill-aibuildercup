import { formatCredits } from "@/lib/money";
import { cn } from "@/lib/cn";

export interface MoneyProps {
  /** Integer cents of credits. */
  cents: number;
  /** Show the "credits" unit (default true). */
  unit?: boolean;
  /** Prefix positive amounts with "+" (ledger deltas). */
  signed?: boolean;
  /** Color positive amounts green (ledger credits). Negative amounts are always danger-colored. */
  colorPositive?: boolean;
  className?: string;
}

/** `12.50 credits` with monospaced digits; negatives as `−0.40` in the danger color (SPEC §11.3). */
export function Money({ cents, unit = true, signed = false, colorPositive = false, className }: MoneyProps) {
  const negative = cents < 0;
  return (
    <span
      className={cn(
        "whitespace-nowrap",
        negative && "text-danger-text",
        colorPositive && cents > 0 && "text-success-text",
        className,
      )}
    >
      <span className="font-mono tabular-nums">{formatCredits(cents, { signed })}</span>
      {unit ? <span> credits</span> : null}
    </span>
  );
}
