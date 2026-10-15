import { MessageSquare } from "lucide-react";
import { useParams } from "react-router";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";

/** Placeholder created by [web-core]; replaced by [web-visitor] (SPEC §11.2). */
export default function AgentPage() {
  const { slug = "" } = useParams();
  return (
    <PageBody>
      <PageHeader
        title={slug || "Agent"}
        description="Agent profile and chat for /a/:slug."
      />
      <EmptyState icon={<MessageSquare />} title="Coming soon" description="This page is being built." />
    </PageBody>
  );
}
