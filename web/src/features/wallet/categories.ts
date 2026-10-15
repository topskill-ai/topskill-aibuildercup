import type { LedgerCategory, LedgerEntry } from "@/api/types";
import type { BadgeVariant } from "@/components/ui/Badge";

export interface CategoryMeta {
  label: string;
  /** Short plural label for filter chips. */
  chip: string;
  badge: BadgeVariant;
}

/** Ledger categories (SPEC §8.3). The UI always filters by `category`, never by `kind`. */
export const LEDGER_CATEGORIES: Record<LedgerCategory, CategoryMeta> = {
  signup_grant: { label: "Signup grant", chip: "Signup grant", badge: "primary" },
  demo_topup: { label: "Demo top-up", chip: "Top-ups", badge: "primary" },
  visitor_payment: { label: "Agent round", chip: "Agent rounds", badge: "neutral" },
  creator_income: { label: "Creator income", chip: "Creator income", badge: "success" },
  a2a_payment: { label: "A2A payment", chip: "A2A payments", badge: "warning" },
  a2a_income: { label: "A2A income", chip: "A2A income", badge: "success" },
  adjust: { label: "Adjustment", chip: "Adjustments", badge: "outline" },
};

/** Filter chip order on the wallet page. */
export const CATEGORY_FILTERS: LedgerCategory[] = [
  "visitor_payment",
  "a2a_payment",
  "creator_income",
  "a2a_income",
  "demo_topup",
  "signup_grant",
  "adjust",
];

export function categoryMeta(category: string): CategoryMeta {
  return LEDGER_CATEGORIES[category as LedgerCategory] ?? { label: category, chip: category, badge: "outline" };
}

export function isLedgerCategory(value: string | null | undefined): value is LedgerCategory {
  return typeof value === "string" && value in LEDGER_CATEGORIES;
}

/**
 * Secondary line under the memo: who the money went to or came from. Debits name the agent's
 * creator, credits name the payer; grants and top-ups have no counterparty.
 */
export function counterpartyLine(entry: LedgerEntry): string | null {
  const name = entry.counterparty_name;
  switch (entry.category) {
    case "visitor_payment":
    case "a2a_payment":
      return name ? `Paid to ${name}` : null;
    case "creator_income":
      return name ? `From ${name}` : null;
    case "a2a_income":
      return name ? `From ${name} via A2A` : "Via A2A";
    default:
      return null;
  }
}
