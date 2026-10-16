/**
 * P2 guard (SPEC §18.2): Model Armor verdict counters summed over the creator's agents (dashboard).
 */
import { ShieldCheck } from "lucide-react";
import type { AgentSummary } from "@/api/types";
import { Card, CardHeader } from "@/components/ui/Card";
import { formatNumber } from "@/lib/format";
import { sumGuardStats } from "./guard";

export function GuardSummaryCard({ agents, className }: { agents: AgentSummary[]; className?: string }) {
  const g = sumGuardStats(agents.map((a) => a.stats));
  const tiles: [string, number, string][] = [
    ["Screened", g.prompts_screened + g.answers_screened, "messages and answers"],
    ["Blocked", g.prompts_blocked + g.answers_blocked, "never charged"],
    ["Redacted", g.prompts_redacted + g.answers_redacted, "sensitive data hidden"],
    ["Flagged answers", g.answers_flagged, "allowed, for review"],
  ];
  return (
    <Card className={className} data-testid="guard-summary">
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            <ShieldCheck aria-hidden className="h-4 w-4 text-success-text" />
            Safety screening
          </span>
        }
        description="Google Cloud Model Armor verdicts across your agents."
      />
      <dl className="grid grid-cols-2 gap-4 p-5 sm:grid-cols-4">
        {tiles.map(([label, value, hint]) => (
          <div key={label} className="min-w-0">
            <dt className="truncate text-[13px] font-medium text-muted">{label}</dt>
            <dd className="mt-0.5 font-mono text-xl font-semibold tabular-nums">{formatNumber(value)}</dd>
            <dd className="truncate text-xs text-subtle">{hint}</dd>
          </div>
        ))}
      </dl>
      {g.failed_open > 0 ? (
        <p className="px-5 pb-4 text-xs text-subtle">
          {formatNumber(g.failed_open)} check{g.failed_open === 1 ? "" : "s"} skipped while Model Armor was unavailable.
        </p>
      ) : null}
    </Card>
  );
}
