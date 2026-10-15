import { Bot } from "lucide-react";
import { useParams } from "react-router";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";

/** Placeholder created by [web-core]; replaced by [web-studio] (SPEC §11.2). */
export default function AgentDetailPage() {
  const { id = "" } = useParams();
  return (
    <PageBody>
      <PageHeader
        title={`Agent ${id}`}
        description="Overview, settings, knowledge, records, versions and sharing."
        back={{ to: "/studio", label: "Studio" }}
      />
      <EmptyState icon={<Bot />} title="Coming soon" description="This page is being built." />
    </PageBody>
  );
}
