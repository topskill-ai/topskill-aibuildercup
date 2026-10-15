import { ArrowRight, Bot, FileUp, MessagesSquare, Sparkles, Wallet } from "lucide-react";
import { Link } from "react-router";
import { PageBody } from "@/components/layout/PageHeader";
import { buttonClasses } from "@/components/ui/Button";
import { Marketplace } from "@/features/marketplace/Marketplace";

const STEPS = [
  {
    icon: FileUp,
    title: "Drop a skill",
    body: "Upload a SKILL.md with your playbook and documents. Gemini drafts the whole listing in seconds.",
  },
  {
    icon: MessagesSquare,
    title: "People and agents use it",
    body: "Visitors chat on the web with cited answers; other AI agents call it over A2A with a signed budget.",
  },
  {
    icon: Wallet,
    title: "You earn per use",
    body: "Every paid round or call credits your wallet with a revenue share. Failed calls are never charged.",
  },
] as const;

/** Home (SPEC §11.4): hero with two CTAs, "How it works", marketplace grid with search. */
export default function HomePage() {
  return (
    <PageBody>
      <section className="py-6 text-center sm:py-12">
        <div className="mx-auto mb-4 inline-flex items-center gap-1.5 rounded-chip bg-primary-soft px-3 py-1 text-[13px] font-medium text-primary">
          <Sparkles aria-hidden className="h-3.5 w-3.5" />
          Expert skills as paid AI agents
        </div>
        <h1 className="mx-auto max-w-3xl text-[30px] leading-tight font-semibold tracking-display sm:text-5xl">
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

      <section aria-labelledby="how-title" className="mb-12">
        <h2 id="how-title" className="sr-only">
          How it works
        </h2>
        <ol className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {STEPS.map((step, i) => (
            <li key={step.title} className="flex min-w-0 gap-4 rounded-card border border-border bg-surface p-5">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary-soft text-primary">
                <step.icon aria-hidden className="h-5 w-5" />
              </div>
              <div className="min-w-0">
                <div className="text-xs font-semibold tracking-wide text-subtle uppercase">Step {i + 1}</div>
                <h3 className="mt-0.5 text-base font-semibold">{step.title}</h3>
                <p className="mt-1 text-sm text-muted">{step.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <Marketplace />
    </PageBody>
  );
}
