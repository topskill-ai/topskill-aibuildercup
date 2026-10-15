import { LayoutDashboard } from "lucide-react";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";

/** Placeholder created by [web-core]; replaced by [web-studio] (SPEC §11.2). */
export default function StudioDashboardPage() {
  return (
    <PageBody>
      <PageHeader
        title={"Studio"}
        description="Balance, earnings, agents and recent income."
      />
      <EmptyState icon={<LayoutDashboard />} title="Coming soon" description="This page is being built." />
    </PageBody>
  );
}
