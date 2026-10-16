import { ArrowRight, Briefcase, Check, DollarSign, FileUp, MessageCircle, Sparkle, Users, Wallet } from "lucide-react";
import { Link } from "react-router";
import heroIllustration from "@/assets/hero-illustration.webp";
import { buttonClasses } from "@/components/ui/Button";
import { Marketplace } from "@/features/marketplace/Marketplace";
import { cn } from "@/lib/cn";

const HIGHLIGHTS = ["No code required", "A2A protocol ready", "Per-call revenue share"] as const;

const STEPS = [
  {
    icon: FileUp,
    tone: "bg-primary-soft text-primary",
    title: "Drop a skill",
    body: "Upload a SKILL.md with your playbook and documents. Gemini drafts the whole listing in seconds.",
  },
  {
    icon: Users,
    tone: "bg-warning-soft text-warning",
    title: "People and agents use it",
    body: "Visitors chat on the web with cited answers; other AI agents call it over A2A with a signed budget.",
  },
  {
    icon: Wallet,
    tone: "bg-success-soft text-success",
    title: "You earn per use",
    body: "Every paid round or call credits your wallet with a revenue share. Failed calls are never charged.",
  },
] as const;

/** Decorative status card floating over the hero illustration (static copy, hidden on phones). */
function FloatCard({
  icon: Icon,
  tone,
  title,
  note,
  className,
}: {
  icon: typeof DollarSign;
  tone: string;
  title: string;
  note: string;
  className: string;
}) {
  return (
    <div
      className={cn(
        "absolute hidden items-center gap-2.5 rounded-[13px] border border-float-card-border bg-float-card px-4 py-2.5 text-left shadow-lift backdrop-blur-sm sm:flex",
        "animate-floaty motion-reduce:animate-none",
        className,
      )}
    >
      <span className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px]", tone)}>
        <Icon aria-hidden className="h-4 w-4" />
      </span>
      <span className="text-[13px] leading-tight font-semibold text-text">
        {title}
        <small className="mt-0.5 block text-[11.5px] font-medium text-subtle">{note}</small>
      </span>
    </div>
  );
}

function Hero() {
  return (
    <section aria-labelledby="hero-title" className="home-hero">
      <div className="page-container grid items-center gap-12 py-12 text-center sm:py-16 lg:grid-cols-[1.04fr_0.96fr] lg:gap-14 lg:pt-[88px] lg:pb-20 lg:text-left">
        <div className="min-w-0">
          <span className="mb-6 inline-flex items-center gap-2 rounded-chip border border-primary/15 bg-primary-soft px-3.5 py-1.5 text-[13px] font-semibold text-primary dark:border-primary/30">
            <Sparkle aria-hidden className="h-3.5 w-3.5 fill-current" />
            Expert skills as paid AI agents
          </span>
          <h1
            id="hero-title"
            className="mx-auto max-w-[15ch] text-[clamp(34px,4.4vw,58px)] leading-[1.08] font-bold tracking-hero lg:mx-0"
          >
            Turn any expert&apos;s skill into a <span className="home-hero-hl">paid AI agent</span> in 60 seconds
          </h1>
          <p className="mx-auto mt-5 max-w-[47ch] text-base leading-relaxed text-muted sm:text-[17.5px] lg:mx-0">
            Usable by people on the web and by other AI agents over A2A, with grounded answers and per-call revenue
            sharing.
          </p>
          <div className="mt-8 flex flex-col items-stretch justify-center gap-3.5 sm:flex-row sm:flex-wrap sm:items-center lg:justify-start">
            <Link to="/studio/new" className={buttonClasses({ variant: "cta", size: "xl", className: "group" })}>
              Publish a skill
              <ArrowRight aria-hidden className="h-4 w-4 transition-transform duration-150 group-hover:translate-x-[3px]" />
            </Link>
            <Link
              to="/assistant"
              className={buttonClasses({
                variant: "outline",
                size: "xl",
                className: "hover:-translate-y-0.5 hover:shadow-lift",
              })}
            >
              <Briefcase aria-hidden className="h-4 w-4 text-primary" />
              Try the Enterprise Assistant
            </Link>
          </div>
          <ul
            aria-label="Highlights"
            className="mt-7 flex flex-wrap items-center justify-center gap-x-5 gap-y-2 text-[13.5px] font-medium text-subtle lg:justify-start"
          >
            {HIGHLIGHTS.map((h) => (
              <li key={h} className="inline-flex items-center gap-1.5">
                <Check aria-hidden className="h-[15px] w-[15px] text-success" strokeWidth={2.4} />
                {h}
              </li>
            ))}
          </ul>
        </div>

        <div className="relative mx-auto w-full max-w-[520px] lg:max-w-none">
          <div className="home-hero-art overflow-hidden rounded-[24px]">
            <img
              src={heroIllustration}
              width={889}
              height={1024}
              fetchPriority="high"
              decoding="async"
              alt="An expert publishing her skill as an AI agent, connecting her knowledge to people and other agents"
              className="block h-auto w-full"
            />
          </div>
          <div aria-hidden>
            <FloatCard
              icon={MessageCircle}
              tone="bg-primary-soft text-primary"
              title="Agent call answered"
              note="Grounded, cited response"
              className="top-[9%] -left-2 lg:-left-[26px]"
            />
            <FloatCard
              icon={DollarSign}
              tone="bg-success-soft text-success"
              title="+12.40 credits earned"
              note="Per-call revenue share"
              className="-right-2 bottom-[12%] [animation-delay:2.2s] lg:-right-5"
            />
          </div>
        </div>
      </div>
    </section>
  );
}

function HowItWorks() {
  return (
    <section aria-labelledby="how-title" className="page-container pt-6 pb-16 sm:pb-20">
      <h2 id="how-title" className="sr-only">
        How it works
      </h2>
      <ol className="mx-auto grid max-w-[560px] grid-cols-1 gap-5 lg:max-w-none lg:grid-cols-3 lg:gap-[22px]">
        {STEPS.map((step, i) => (
          <li
            key={step.title}
            className="relative min-w-0 overflow-hidden rounded-card border border-border bg-surface px-[26px] pt-7 pb-[26px] transition-[translate,box-shadow,border-color] duration-200 ease-out hover:-translate-y-1 hover:border-border-strong hover:shadow-lift"
          >
            <span
              aria-hidden
              className="absolute top-3.5 right-5 font-display text-[44px] leading-none font-bold tracking-[-0.04em] text-mist select-none"
            >
              {String(i + 1).padStart(2, "0")}
            </span>
            <span className={cn("mb-[18px] flex h-[42px] w-[42px] items-center justify-center rounded-[12px]", step.tone)}>
              <step.icon aria-hidden className="h-5 w-5" />
            </span>
            <div className="mb-2 text-[11.5px] font-bold tracking-[0.09em] text-subtle uppercase">Step {i + 1}</div>
            <h3 className="mb-2 text-[17.5px] font-semibold">{step.title}</h3>
            <p className="text-sm leading-relaxed text-muted">{step.body}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

/** Home (SPEC §11.4): two-column hero with illustration, "How it works" steps, marketplace grid with search. */
export default function HomePage() {
  return (
    <>
      <Hero />
      <HowItWorks />
      <div className="page-container pb-20 sm:pb-24">
        <Marketplace />
      </div>
    </>
  );
}
