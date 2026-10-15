/**
 * Money helpers. Amounts are always integer cents of simulated credits (SPEC conventions);
 * 100 cents = 1.00 credit. Never use floats for amounts.
 */

const MINUS = "−";

function assertInt(cents: number): number {
  if (!Number.isFinite(cents)) return 0;
  return Math.trunc(cents);
}

/** `1250` → `"12.50"`, `-40` → `"−0.40"`. Thousands are grouped: `123456` → `"1,234.56"`. */
export function formatCredits(cents: number, opts: { signed?: boolean } = {}): string {
  const value = assertInt(cents);
  const abs = Math.abs(value);
  const whole = Math.floor(abs / 100).toLocaleString("en-US");
  const frac = String(abs % 100).padStart(2, "0");
  const sign = value < 0 ? MINUS : opts.signed && value > 0 ? "+" : "";
  return `${sign}${whole}.${frac}`;
}

/** `1250` → `"12.50 credits"`. */
export function formatCreditsWithUnit(cents: number, opts: { signed?: boolean } = {}): string {
  return `${formatCredits(cents, opts)} credits`;
}

/**
 * Creator revenue share in integer cents, floored exactly like the backend:
 * `share = paid * bp // 10000` (SPEC §8.1). Read `bp` from `meta.revenue_share_bp`, never hardcode it.
 */
export function creatorShareCents(paidCents: number, revenueShareBp: number): number {
  const paid = assertInt(paidCents);
  const bp = assertInt(revenueShareBp);
  if (paid <= 0 || bp <= 0) return 0;
  return Math.floor((paid * bp) / 10000);
}

/** `8000` → `"80%"`, `7550` → `"75.5%"`. */
export function formatShareBp(bp: number): string {
  const pct = assertInt(bp) / 100;
  return `${Number.isInteger(pct) ? pct.toFixed(0) : String(pct)}%`;
}

/**
 * Parse a user-typed credit amount ("1", "1.5", "1.50", "1,000.25") into integer cents.
 * Returns null for anything that is not a non-negative amount with at most 2 decimals.
 */
export function parseCreditsToCents(input: string): number | null {
  const s = input.trim().replace(/,/g, "");
  const m = /^(\d+)(?:\.(\d{0,2}))?$/.exec(s);
  if (!m) return null;
  const whole = Number(m[1]);
  const frac = Number((m[2] ?? "").padEnd(2, "0"));
  const cents = whole * 100 + frac;
  return Number.isSafeInteger(cents) ? cents : null;
}
