/** Share: public page URL, A2A Agent Card URL and endpoint (MUST), card JSON preview and curl (SHOULD). */
import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { throwForStatus } from "@/api/client";
import { ErrorCodes, hasCode } from "@/api/errors";
import type { Agent } from "@/api/types";
import { Badge } from "@/components/ui/Badge";
import { buttonClasses } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { CopyField } from "@/components/ui/CopyField";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Skeleton } from "@/components/ui/Skeleton";
import { a2aPriceLabel } from "./labels";

async function fetchCard(url: string, signal: AbortSignal): Promise<unknown> {
  const res = await fetch(url, { signal, credentials: "omit", headers: { Accept: "application/json" } });
  // Keeps the JSON error envelope (code, message, trace id) so the preview error shows a trace id.
  await throwForStatus(res);
  return res.json();
}

/** A copy-pasteable paid call (A2A v0.3 `message/send`, mandate in the `X-AP2-Mandate` header). */
function curlExample(agent: Agent): string {
  return [
    `curl -s ${agent.urls.a2a_endpoint} \\`,
    `  -H 'Content-Type: application/json' \\`,
    `  -H "X-AP2-Mandate: $MANDATE" \\`,
    `  -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"messageId":"'"$(uuidgen)"'","role":"user","kind":"message","parts":[{"kind":"text","text":"Hello"}]}}}'`,
  ].join("\n");
}

export function ShareTab({ agent }: { agent: Agent }) {
  const a2a = agent.pricing.a2a_enabled;
  const card = useQuery({
    queryKey: ["agent-card", agent.urls.a2a_card, agent.updated_at, agent.active_version],
    queryFn: ({ signal }) => fetchCard(agent.urls.a2a_card, signal),
    enabled: a2a,
    retry: false,
    staleTime: 60_000,
  });

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Public page"
          description="People chat with your agent here. Share it anywhere."
          actions={
            <a
              href={agent.urls.public_page}
              target="_blank"
              rel="noreferrer"
              className={buttonClasses({ variant: "outline", size: "sm" })}
            >
              <ExternalLink aria-hidden className="h-4 w-4" />
              Open
            </a>
          }
        />
        <CardBody>
          <CopyField label="Web URL" value={agent.urls.public_page} />
          {agent.status !== "active" ? (
            <p className="mt-2 text-[13px] text-warning">The agent is paused, so the page shows it as unavailable.</p>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Agent-to-agent (A2A)"
          description="Other AI agents discover your agent through its Agent Card and pay per call with a signed mandate."
          actions={<Badge variant={a2a ? "primary" : "neutral"}>{a2aPriceLabel(agent.pricing)}</Badge>}
        />
        <CardBody className="space-y-4">
          {a2a ? (
            <>
              <CopyField label="Agent Card URL" value={agent.urls.a2a_card} />
              <CopyField label="JSON-RPC endpoint" value={agent.urls.a2a_endpoint} />
            </>
          ) : (
            <p className="text-sm text-muted">A2A is off. Turn it on in Settings → Pricing to share an Agent Card.</p>
          )}
        </CardBody>
      </Card>

      {a2a ? (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Card className="min-w-0">
            <CardHeader title="Agent Card preview" description="What callers read before hiring your agent." />
            <CardBody>
              {card.isPending ? (
                <Skeleton className="h-48 w-full" />
              ) : card.error ? (
                <ErrorLine
                  error={card.error}
                  message={
                    hasCode(card.error, ErrorCodes.agent_offline)
                      ? "The Agent Card is offline while the agent is paused."
                      : undefined
                  }
                  onRetry={() => void card.refetch()}
                />
              ) : (
                <pre className="max-h-96 overflow-auto rounded-control bg-slate-900 p-4 font-mono text-xs leading-5 text-slate-100">
                  {JSON.stringify(card.data, null, 2)}
                </pre>
              )}
            </CardBody>
          </Card>
          <Card className="min-w-0">
            <CardHeader
              title="Call it with curl"
              description="Get a mandate token from POST /api/mandates while signed in, then send a message."
            />
            <CardBody>
              <pre className="overflow-auto rounded-control bg-slate-900 p-4 font-mono text-xs leading-5 whitespace-pre text-slate-100">
                {curlExample(agent)}
              </pre>
            </CardBody>
          </Card>
        </div>
      ) : null}
    </div>
  );
}
