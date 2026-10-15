import { Bot, RotateCcw, ShieldCheck, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useMeta } from "@/api/meta";
import type { Mandate } from "@/api/types";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import type { CreateMandateResponse } from "@/features/mandates/api";
import { effectiveStatus } from "@/features/mandates/model";
import { AuthoriseBudgetDialog } from "@/features/assistant/AuthoriseBudgetDialog";
import { Composer } from "@/features/assistant/Composer";
import { MandateCard, MandateStrip, MiniLedger } from "@/features/assistant/MandatePanel";
import { liveMandate } from "@/features/assistant/timeline";
import { TurnView } from "@/features/assistant/TurnView";
import { useAssistant, type ActiveMandate } from "@/features/assistant/useAssistant";
import { formatShareBp } from "@/lib/money";

const EXAMPLES = [
  "Our new hire from Brazil needs a work visa plan, a resume review and a quick check of the offer letter",
  "Review this resume summary for a senior data engineer role and tell me what is missing",
  "What does a software engineer from India need to get a work visa for our Berlin office?",
];

const STEPS = [
  { title: "You authorise a budget", text: "A signed mandate: total budget, per-call limit, expiry." },
  { title: "It discovers specialists", text: "Ranks TopSkill agents and reads their A2A Agent Cards." },
  { title: "It pays per call over A2A", text: "Each call carries the mandate. Rejected calls cost nothing." },
];

function EmptyIntro({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="relative overflow-clip rounded-card border border-border bg-surface p-6 sm:p-8">
      <div aria-hidden className="pointer-events-none absolute -right-24 -top-24 h-64 w-64 rounded-full bg-indigo-100 blur-3xl" />
      <div aria-hidden className="pointer-events-none absolute -bottom-28 -left-16 h-64 w-64 rounded-full bg-violet-100 blur-3xl" />
      <div className="relative">
        <div className="flex h-11 w-11 items-center justify-center rounded-card bg-gradient-to-br from-indigo-500 to-violet-500 text-white">
          <Bot aria-hidden className="h-5 w-5" />
        </div>
        <h2 className="mt-4 text-xl font-semibold sm:text-2xl">Your company assistant can hire experts</h2>
        <p className="mt-1.5 max-w-xl text-[15px] text-muted">
          Ask for something no single assistant knows well. It finds specialist agents on TopSkill, pays each one per call within
          the budget you sign, and combines their answers.
        </p>
        <ol className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
          {STEPS.map((s, i) => (
            <li key={s.title} className="min-w-0 rounded-control border border-border bg-surface/80 p-3">
              <div className="flex items-start gap-2 text-sm leading-5 font-semibold">
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary-soft text-xs text-primary">{i + 1}</span>
                <span className="min-w-0">{s.title}</span>
              </div>
              <p className="mt-1 text-[13px] text-muted">{s.text}</p>
            </li>
          ))}
        </ol>
        <div className="mt-6">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold tracking-wide text-subtle uppercase">
            <Sparkles aria-hidden className="h-3.5 w-3.5" /> Try a task
          </div>
          <div className="flex flex-col gap-2">
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                type="button"
                onClick={() => onPick(ex)}
                className="rounded-control border border-border bg-surface px-3 py-2 text-left text-sm text-text transition-colors hover:border-primary hover:bg-primary-soft/50"
              >
                {ex}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function usable(active: ActiveMandate | null, live: Mandate | null): active is ActiveMandate {
  return Boolean(active && live && !active.dead && effectiveStatus(live, Date.now()) === "active");
}

/** `/assistant` — Enterprise Assistant demo (SPEC §9.4, §11.4). */
export default function AssistantPage() {
  const a = useAssistant();
  const meta = useMeta();
  const [draft, setDraft] = useState("");
  const [dialog, setDialog] = useState<{ open: boolean; task: string; sendAfter: boolean }>({
    open: false,
    task: "",
    sendAfter: false,
  });
  const endRef = useRef<HTMLDivElement | null>(null);
  const live = a.active ? liveMandate(a.active.mandate, a.counters) : null;

  // Follow the timeline while it grows, until the user scrolls up to read; scrolling back to the
  // bottom (or starting a new turn) resumes following.
  const follow = useRef(true);
  useEffect(() => {
    const nearBottom = () => window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160;
    const onWheel = (e: WheelEvent) => {
      if (e.deltaY < 0) follow.current = false;
    };
    const onScroll = () => {
      if (nearBottom()) follow.current = true;
    };
    const onTouch = () => {
      follow.current = nearBottom();
    };
    window.addEventListener("wheel", onWheel, { passive: true });
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("touchmove", onTouch, { passive: true });
    return () => {
      window.removeEventListener("wheel", onWheel);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("touchmove", onTouch);
    };
  }, []);
  const lastTurn = a.turns[a.turns.length - 1];
  const turnCount = a.turns.length;
  useEffect(() => {
    if (turnCount > 0) follow.current = true;
  }, [turnCount]);
  useEffect(() => {
    if (!lastTurn || !follow.current) return;
    endRef.current?.scrollIntoView({ block: "end", behavior: lastTurn.status === "streaming" ? "smooth" : "auto" });
  }, [lastTurn]);

  const submit = (text: string) => {
    if (a.streaming) return;
    if (!usable(a.active, live)) {
      setDialog({ open: true, task: text, sendAfter: true });
      return;
    }
    setDraft("");
    void a.send(text, a.active.token, a.active.mandate.id);
  };

  const onAuthorised = (res: CreateMandateResponse) => {
    a.authorised(res);
    const task = dialog.task.trim();
    if (dialog.sendAfter && task) {
      setDraft("");
      void a.send(task, res.token, res.mandate.id);
    }
  };

  const openAuthorise = () => setDialog({ open: true, task: draft, sendAfter: false });

  return (
    <PageBody>
      <PageHeader
        eyebrow="A2A · AP2-style mandates"
        title="Enterprise Assistant"
        description="Hire specialist agents over A2A, paid per call within a budget you authorise."
        actions={
          a.turns.length > 0 ? (
            <Button variant="outline" size="sm" icon={<RotateCcw aria-hidden className="h-4 w-4" />} onClick={a.reset} disabled={a.streaming}>
              New conversation
            </Button>
          ) : null
        }
      />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex min-w-0 flex-col">
          <MandateStrip active={a.active} counters={a.counters} />
          <div className="flex-1 space-y-8" aria-live="polite">
            {a.turns.length === 0 ? <EmptyIntro onPick={submit} /> : null}
            {a.turns.map((t) => (
              <TurnView
                key={t.id}
                turn={t}
                onReauthorise={(task) => !a.streaming && setDialog({ open: true, task, sendAfter: true })}
              />
            ))}
          </div>
          <div ref={endRef} aria-hidden className="scroll-mb-36" />
          <div className="sticky bottom-0 z-10 -mx-1 mt-6 bg-gradient-to-t from-bg via-bg to-transparent px-1 pt-4 pb-4">
            <Composer
              value={draft}
              onChange={setDraft}
              onSubmit={submit}
              onStop={a.stop}
              streaming={a.streaming}
              hint={
                usable(a.active, live)
                  ? "Paid from your mandate per call. Calls over the limit are rejected and never charged."
                  : "You will authorise a budget before the assistant hires anyone."
              }
            />
          </div>
        </div>
        <aside id="mandate-panel" aria-label="Mandate and payments" className="min-w-0 space-y-4 lg:sticky lg:top-20 lg:self-start">
          <MandateCard
            active={a.active}
            counters={a.counters}
            streaming={a.streaming}
            onAuthorise={openAuthorise}
            onRevoked={a.markRevoked}
          />
          <MiniLedger streaming={a.streaming} />
          <p className="flex items-start gap-2 px-1 text-2xs text-subtle">
            <ShieldCheck aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            The assistant's own reasoning is free. Only A2A calls are charged to your wallet
            {meta.data ? `, and ${formatShareBp(meta.data.revenue_share_bp)} of each goes to the agent's creator` : ""}.
          </p>
        </aside>
      </div>
      <AuthoriseBudgetDialog
        open={dialog.open}
        onOpenChange={(open) => setDialog((d) => ({ ...d, open }))}
        task={dialog.task}
        onAuthorised={onAuthorised}
      />
    </PageBody>
  );
}
