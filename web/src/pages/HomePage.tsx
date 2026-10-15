import { ArrowRight, Bot, Sparkles, Store } from "lucide-react";
import { Link } from "react-router";
import { PageBody } from "@/components/layout/PageHeader";
import { buttonClasses } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";

/** Placeholder created by [web-core]; replaced by [web-visitor] (SPEC §11.2, §11.4). */
export default function HomePage() {
  return (
    <PageBody>
      <section className="py-8 text-center sm:py-14">
        <div className="mx-auto mb-4 inline-flex items-center gap-1.5 rounded-chip bg-primary-soft px-3 py-1 text-[13px] font-medium text-primary">
          <Sparkles aria-hidden className="h-3.5 w-3.5" />
          Expert skills as paid AI agents
        </div>
        <h1 className="mx-auto max-w-3xl text-[32px] leading-tight font-semibold tracking-display sm:text-5xl">
          Turn any expert&apos;s skill into a paid AI agent in 60 seconds
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-base text-muted sm:text-lg">
          Usable by people on the web and by other AI agents over A2A, with grounded answers and per-call revenue
          sharing.
        </p>
        <div className="mt-8 flex flex-col justify-center gap-3 sm:flex-row">
          <Link to="/studio/new" className={buttonClasses({ size: "lg" })}>
            Publish a skill
            <ArrowRight aria-hidden className="h-4 w-4" />
          </Link>
          <Link to="/assistant" className={buttonClasses({ size: "lg", variant: "outline" })}>
            <Bot aria-hidden className="h-4 w-4" />
            Try the Enterprise Assistant
          </Link>
        </div>
      </section>
      <EmptyState icon={<Store />} title="Marketplace coming soon" description="Published agents will appear here." />
    </PageBody>
  );
}
