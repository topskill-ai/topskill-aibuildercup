import { useSearchParams } from "react-router";
import type { LedgerCategory } from "@/api/types";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { MandateList } from "@/features/mandates/MandateList";
import { BalanceCard } from "@/features/wallet/BalanceCard";
import { isLedgerCategory } from "@/features/wallet/categories";
import { CategoryChips, LedgerTable } from "@/features/wallet/LedgerTable";

/** `/wallet` (SPEC §11.2, §11.4): balance + demo top-up, ledger with category chips, mandates. */
export default function WalletPage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("category");
  const category: LedgerCategory | null = isLedgerCategory(raw) ? raw : null;

  const setCategory = (next: LedgerCategory | null) => {
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (next) p.set("category", next);
        else p.delete("category");
        return p;
      },
      { replace: true },
    );
  };

  return (
    <PageBody>
      <PageHeader
        title="Wallet"
        description="Your credit balance, every charge and payout, and the budgets you authorised for agents."
      />
      <div className="space-y-6">
        <BalanceCard />

        <section aria-labelledby="ledger-title" className="rounded-card border border-border bg-surface">
          <div className="space-y-3 px-5 pt-5 pb-4">
            <div>
              <h2 id="ledger-title" className="text-base font-semibold">
                Ledger
              </h2>
              <p className="mt-0.5 text-sm text-muted">Newest first. Every entry is written in the same transaction as the charge.</p>
            </div>
            <CategoryChips value={category} onChange={setCategory} />
          </div>
          <div className="border-t border-border">
            <LedgerTable category={category} />
          </div>
        </section>

        <MandateList />
      </div>
    </PageBody>
  );
}
