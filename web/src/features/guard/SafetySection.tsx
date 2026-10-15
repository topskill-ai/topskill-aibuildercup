/**
 * P2 guard (SPEC §18 "Guard"): Studio → Settings "Safety" card. The switch saves at once and applies live
 * (no new version needed); the counters come from `agent.stats.guard`.
 */
import { useMutation } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import type { Agent } from "@/api/types";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Switch } from "@/components/ui/Switch";
import { formatNumber } from "@/lib/format";
import { toast } from "@/lib/toast";
import { agentApi, useAgentCacheUpdater } from "@/features/studio/api";
import { guardStats } from "./guard";

export function GuardCounters({ agent, className }: { agent: Agent; className?: string }) {
  const g = guardStats(agent.stats);
  const rows: [string, number][] = [
    ["Messages screened", g.prompts_screened],
    ["Messages blocked", g.prompts_blocked],
    ["Messages redacted", g.prompts_redacted],
    ["Answers screened", g.answers_screened],
    ["Answers redacted", g.answers_redacted],
    ["Answers withheld", g.answers_blocked],
    ["Skipped (screening unavailable)", g.failed_open],
  ];
  return (
    <dl className={className ?? "grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3"} data-testid="guard-counters">
      {rows.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="truncate text-[13px] text-muted">{label}</dt>
          <dd className="font-mono font-medium tabular-nums">{formatNumber(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function SafetySection({ agent }: { agent: Agent }) {
  const update = useAgentCacheUpdater();
  const enabled = agent.config.safety_screening !== false;
  const save = useMutation({
    mutationFn: (on: boolean) => agentApi.update(agent.id, { config: { safety_screening: on } }),
    onSuccess: (a) => {
      update(a);
      toast.success(a.config.safety_screening ? "Safety screening is on." : "Safety screening is off.");
    },
  });

  return (
    <Card>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            <ShieldCheck aria-hidden className="h-4 w-4 text-success" />
            Safety
          </span>
        }
        description="Applies immediately, no new version needed. Web chat: visitor messages and answers are screened. A2A: incoming messages are screened."
      />
      <CardBody className="space-y-5">
        <Switch
          checked={save.isPending ? !!save.variables : enabled}
          disabled={save.isPending}
          onCheckedChange={(on) => save.mutate(on)}
          label="Safety screening (Model Armor)"
          description="Google Cloud Model Armor checks each incoming message for prompt injection, jailbreaks and unsafe content before the model runs, and redacts sensitive data such as card numbers. Web chat answers are also screened (A2A answers are not). Blocked messages are never charged."
        />
        {enabled && agent.guard_active === false ? (
          <p className="text-[13px] text-warning" data-testid="guard-unavailable">
            Screening is unavailable on this deployment right now, so messages pass unscreened.
          </p>
        ) : null}
        {save.error ? <ErrorLine error={save.error} /> : null}
        <GuardCounters agent={agent} />
      </CardBody>
    </Card>
  );
}
