import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import type { A2ACall } from "@/api/types";
import { Avatar } from "@/components/ui/Avatar";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Money } from "@/components/ui/Money";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { cn } from "@/lib/cn";
import { formatRelative, truncate } from "@/lib/format";
import { useAgentLook } from "./agentLook";
import { isMissingEndpoint, useMandateCalls } from "./api";

function StatusIcon({ status }: { status: A2ACall["status"] }) {
  if (status === "completed") return <CheckCircle2 aria-label="Completed" className="h-4 w-4 text-success" />;
  if (status === "failed") return <XCircle aria-label="Failed" className="h-4 w-4 text-danger" />;
  return <Loader2 aria-label="Running" className="h-4 w-4 animate-spin text-primary" />;
}

export function A2ACallRow({ call }: { call: A2ACall }) {
  const look = useAgentLook(call.agent_slug);
  return (
    <li className="flex items-start gap-3 py-2.5">
      <Avatar src={look.avatarUrl} name={call.agent_title || call.agent_slug} accent={look.accent} size={28} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="flex min-w-0 items-center gap-1.5 font-medium">
            <StatusIcon status={call.status} />
            <span className="truncate">{call.agent_title || call.agent_slug}</span>
          </span>
          <span className={cn("shrink-0 text-[13px]", call.status === "failed" && "text-subtle")}>
            {call.status === "failed" ? (
              "Not charged"
            ) : (
              <Money cents={-call.charged_cents} unit={false} />
            )}
          </span>
        </div>
        {call.request_preview ? (
          <p className="truncate text-[13px] text-muted" title={call.request_preview}>
            {truncate(call.request_preview, 120)}
          </p>
        ) : null}
        <p className="text-2xs text-subtle">
          {formatRelative(call.created_at)}
          {call.creator_share_cents > 0 ? (
            <>
              {" · creator received "}
              <Money cents={call.creator_share_cents} unit={false} />
            </>
          ) : null}
        </p>
      </div>
    </li>
  );
}

/** A2A calls paid with one mandate, newest first ([SHOULD] `GET /api/mandates/{id}/calls`). */
export function A2ACallList({ mandateId, refetchInterval }: { mandateId: string; refetchInterval?: number | false }) {
  const calls = useMandateCalls(mandateId, { refetchInterval });
  if (calls.isPending) return <SkeletonRows rows={2} className="py-2" />;
  if (calls.isError) {
    if (isMissingEndpoint(calls.error)) return null;
    return <ErrorLine error={calls.error} onRetry={() => void calls.refetch()} className="py-2" />;
  }
  const note = (
    <p className="py-2 text-2xs text-subtle">
      Calls stopped by the assistant's pre-flight check never reach the agent, cost nothing and are not listed here.
    </p>
  );
  if (calls.data.items.length === 0)
    return (
      <>
        <p className="py-2 text-[13px] text-subtle">No calls yet.</p>
        {note}
      </>
    );
  return (
    <>
      <ul aria-label="A2A calls" className="divide-y divide-border">
        {calls.data.items.map((c) => (
          <A2ACallRow key={c.id} call={c} />
        ))}
      </ul>
      {note}
    </>
  );
}
