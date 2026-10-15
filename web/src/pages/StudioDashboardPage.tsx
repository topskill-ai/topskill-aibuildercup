/**
 * Creator dashboard (SPEC §11.4): KPI tiles, income by channel, agents table and recent income.
 * Polls every 5 s while the tab is visible and highlights income that just arrived.
 */
import { Activity, Bot, CalendarDays, Coins, Plus, Rocket, Sparkles, Wallet } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import type { LedgerEntry } from "@/api/types";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { buttonClasses } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Money } from "@/components/ui/Money";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { Stat } from "@/components/ui/Stat";
import { Switch } from "@/components/ui/Switch";
import { formatNumber } from "@/lib/format";
import { safeLocal } from "@/lib/safeStorage";
import { formatCredits } from "@/lib/money";
import { AskDataCard } from "@/features/analytics/AskDataCard";
import { AgentsTable } from "@/features/studio/AgentsTable";
import { useDashboard } from "@/features/studio/api";
import { IncomeList } from "@/features/studio/IncomeList";
import { channelSplit, newEntryIds } from "@/features/studio/income";
import { GuardSummaryCard } from "@/features/guard/GuardSummaryCard";

const LIVE_KEY = "topskill.studio.live";

function ChannelBar({ visitorCents, a2aCents }: { visitorCents: number; a2aCents: number }) {
  const s = channelSplit(visitorCents, a2aCents);
  return (
    <div>
      <div
        className="flex h-3 overflow-hidden rounded-chip bg-slate-100"
        role="img"
        aria-label={`Visitors ${s.visitorPct}%, A2A ${s.a2aPct}%`}
      >
        {s.totalCents > 0 ? (
          <>
            <div className="h-full bg-primary transition-[width] duration-500" style={{ width: `${s.visitorPct}%` }} />
            <div className="h-full bg-accent-violet transition-[width] duration-500" style={{ width: `${s.a2aPct}%` }} />
          </>
        ) : null}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-muted">
            <span className="h-2.5 w-2.5 rounded-full bg-primary" aria-hidden /> Visitors (web rounds)
          </div>
          <div className="mt-0.5 font-medium">
            <Money cents={s.visitorCents} /> <span className="text-subtle">· {s.visitorPct}%</span>
          </div>
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-muted">
            <span className="h-2.5 w-2.5 rounded-full bg-accent-violet" aria-hidden /> AI agents (A2A)
          </div>
          <div className="mt-0.5 font-medium">
            <Money cents={s.a2aCents} /> <span className="text-subtle">· {s.a2aPct}%</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function StudioDashboardPage() {
  const [live, setLive] = useState(() => safeLocal.get(LIVE_KEY) !== "0");
  const q = useDashboard({ live });
  const d = q.data;

  // Highlight income rows that arrived since the previous fetch (cleared after a moment).
  const [seen, setSeen] = useState<{ items?: LedgerEntry[]; fresh: Set<string> }>({ fresh: new Set() });
  const items = d?.recent_income;
  if (items && items !== seen.items) setSeen({ items, fresh: newEntryIds(seen.items, items) });
  useEffect(() => {
    if (seen.fresh.size === 0) return;
    const t = setTimeout(() => setSeen((s) => ({ ...s, fresh: new Set() })), 4000);
    return () => clearTimeout(t);
  }, [seen.fresh]);

  const loading = q.isPending;

  return (
    <PageBody>
      <PageHeader
        title="Studio"
        description="Your balance, earnings from people and AI agents, and your published agents."
        actions={
          <>
            <Switch
              checked={live}
              onCheckedChange={(on) => {
                setLive(on);
                safeLocal.set(LIVE_KEY, on ? "1" : "0");
              }}
              label={
                <span className="inline-flex items-center gap-1.5">
                  {live ? <span className="h-2 w-2 animate-pulse rounded-full bg-success" aria-hidden /> : null}
                  Live
                </span>
              }
            />
          </>
        }
      />

      {q.error ? <ErrorLine className="mb-6" error={q.error} onRetry={() => void q.refetch()} /> : null}

      <section aria-label="Key figures" className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-5">
        <Stat label="Balance" icon={<Wallet />} loading={loading} value={<Money cents={d?.balance_cents ?? 0} unit={false} />} hint="credits" />
        <Stat
          label="Earned today"
          icon={<Coins />}
          loading={loading}
          value={<Money cents={d?.earned_today_cents ?? 0} unit={false} />}
          hint="UTC day"
        />
        <Stat
          label="Earned 7 days"
          icon={<CalendarDays />}
          loading={loading}
          value={<Money cents={d?.earned_7d_cents ?? 0} unit={false} />}
          hint={d ? `All time: ${formatCredits(d.earned_total_cents)}` : undefined}
        />
        <Stat label="Paid rounds" icon={<Activity />} loading={loading} value={formatNumber(d?.paid_rounds ?? 0)} hint="web visitors" />
        <Stat
          label="A2A calls"
          icon={<Bot />}
          loading={loading}
          value={formatNumber(d?.a2a_calls ?? 0)}
          hint="paid by AI agents"
          className="col-span-2 lg:col-span-1"
        />
      </section>

      <AskDataCard className="mt-6" />

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <Card className="h-max">
          <CardHeader title="Income by channel" description="All-time creator income, split by who paid." />
          <div className="p-5">
            {loading ? (
              <SkeletonRows rows={1} />
            ) : (
              <ChannelBar visitorCents={d?.visitor_income_cents ?? 0} a2aCents={d?.a2a_income_cents ?? 0} />
            )}
          </div>
        </Card>

        <Card className="h-max">
          <CardHeader title="Recent income" description="Visitor rounds and A2A calls, newest first." />
          <div className="pt-2 pb-1">
            {loading ? (
              <SkeletonRows rows={4} className="p-5" />
            ) : d && d.recent_income.length > 0 ? (
              <IncomeList items={d.recent_income} freshIds={seen.fresh} />
            ) : (
              <p className="px-5 pb-5 text-sm text-muted">
                No income yet. Share your agent's link or make it callable over A2A to start earning.
              </p>
            )}
          </div>
        </Card>
      </div>

      <div className="mt-6">
        <Card>
          <CardHeader
            title="Your agents"
            actions={
              d && d.agents.length > 0 ? (
                <Link to="/studio/new" className={buttonClasses({ variant: "outline", size: "sm" })}>
                  <Plus aria-hidden className="h-4 w-4" />
                  Publish a skill
                </Link>
              ) : null
            }
          />
          <div className="pt-3">
            {loading ? (
              <SkeletonRows rows={3} className="p-5" />
            ) : d && d.agents.length > 0 ? (
              <AgentsTable agents={d.agents} />
            ) : (
              <div className="p-5">
                <EmptyState
                  icon={<Rocket />}
                  title="Publish your first agent"
                  description="Drop a SKILL.md and Gemini drafts the whole listing. It takes about a minute."
                  action={
                    <Link to="/studio/new" className={buttonClasses()}>
                      <Sparkles aria-hidden className="h-4 w-4" />
                      Publish a skill
                    </Link>
                  }
                />
              </div>
            )}
          </div>
        </Card>
      </div>

      {d && d.agents.length > 0 ? <GuardSummaryCard className="mt-6" agents={d.agents} /> : null}
    </PageBody>
  );
}
