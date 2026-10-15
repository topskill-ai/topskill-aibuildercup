import { Bot } from "lucide-react";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";

/** Placeholder created by [web-core]; replaced by [web-assistant] (SPEC §11.2). */
export default function AssistantPage() {
  return (
    <PageBody>
      <PageHeader
        title={"Enterprise Assistant"}
        description="Hire specialist agents over A2A within a budget you authorise."
      />
      <EmptyState icon={<Bot />} title="Coming soon" description="This page is being built." />
    </PageBody>
  );
}
