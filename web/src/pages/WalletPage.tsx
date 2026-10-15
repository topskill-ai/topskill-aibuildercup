import { Wallet } from "lucide-react";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";

/** Placeholder created by [web-core]; replaced by [web-assistant] (SPEC §11.2). */
export default function WalletPage() {
  return (
    <PageBody>
      <PageHeader
        title={"Wallet"}
        description="Balance, demo top-up, ledger and mandates."
      />
      <EmptyState icon={<Wallet />} title="Coming soon" description="This page is being built." />
    </PageBody>
  );
}
