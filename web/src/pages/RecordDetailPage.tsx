import { ClipboardList } from "lucide-react";
import { useParams } from "react-router";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";

/** Placeholder created by [web-core]; replaced by [web-studio] (SPEC §11.2). */
export default function RecordDetailPage() {
  const { id = "", recordId = "" } = useParams();
  return (
    <PageBody>
      <PageHeader
        title={`Record ${recordId}`}
        description="One captured record with its transcript."
        back={{ to: `/studio/agents/${id}?tab=records`, label: "Records" }}
      />
      <EmptyState icon={<ClipboardList />} title="Coming soon" description="This page is being built." />
    </PageBody>
  );
}
