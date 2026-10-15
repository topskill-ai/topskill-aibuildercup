import { Sparkles } from "lucide-react";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";

/** Placeholder created by [web-core]; replaced by [web-studio] (SPEC §11.2). */
export default function PublishWizardPage() {
  return (
    <PageBody>
      <PageHeader
        title={"Publish a skill"}
        description="Upload a SKILL.md, let Gemini draft the listing, set pricing and go live."
      />
      <EmptyState icon={<Sparkles />} title="Coming soon" description="This page is being built." />
    </PageBody>
  );
}
