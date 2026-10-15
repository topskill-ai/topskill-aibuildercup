/**
 * One captured record with its transcript and creator-only follow-up (SPEC §11.2 [SHOULD]).
 * Renders the row handed over by the Records table immediately, then loads the full record.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, Save } from "lucide-react";
import { useState } from "react";
import { useLocation, useParams } from "react-router";
import { isApiError } from "@/api/errors";
import type { ChatMessage, FollowStatus, RecordRow, RecordSchema } from "@/api/types";
import { Markdown } from "@/components/Markdown";
import { PageBody, PageHeader, PageLoading } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Field } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { Textarea } from "@/components/ui/Textarea";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { toast } from "@/lib/toast";
import { FOLLOW_STATUS_LABELS, patchRecord, useRecordDetail } from "@/features/records/api";
import { FollowBadge, StatusBadge } from "@/features/records/RecordsTab";
import { formatRecordValue } from "@/features/records/schema";
import { useAgent } from "@/features/studio/api";

function Transcript({ messages }: { messages: ChatMessage[] }) {
  if (messages.length === 0) return <p className="text-sm text-muted">No messages.</p>;
  return (
    <ol className="space-y-4">
      {messages.map((m) => (
        <li key={m.id} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
          <div
            className={cn(
              "max-w-[85%] min-w-0 rounded-card px-4 py-2.5 text-sm",
              m.role === "user" ? "bg-primary text-primary-fg" : "border border-border bg-surface",
            )}
          >
            {m.role === "user" ? (
              <p className="whitespace-pre-wrap break-words">{m.text}</p>
            ) : (
              <Markdown text={m.text} citations={m.citations} />
            )}
            <div className={cn("mt-1 text-2xs", m.role === "user" ? "text-indigo-100" : "text-subtle")}>
              {formatDateTime(m.created_at)}
              {m.status !== "ok" ? ` · ${m.status}` : ""}
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}

function FollowCard({ agentId, row }: { agentId: string; row: RecordRow }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<FollowStatus>(row.follow_status);
  const [note, setNote] = useState(row.follow_note);
  const m = useMutation({
    mutationFn: () => patchRecord(agentId, row.id, { follow_status: status, follow_note: note }),
    onSuccess: () => {
      toast.success("Follow-up saved");
      void qc.invalidateQueries({ queryKey: ["records", agentId] });
    },
  });
  const dirty = status !== row.follow_status || note !== row.follow_note;
  return (
    <Card>
      <CardHeader title="Follow-up" description="Only you see this." />
      <CardBody className="space-y-4">
        <Field label="Status">
          {(c) => (
            <Select
              id={c.id}
              value={status}
              options={(Object.keys(FOLLOW_STATUS_LABELS) as FollowStatus[]).map((k) => ({
                value: k,
                label: FOLLOW_STATUS_LABELS[k],
              }))}
              onChange={(e) => setStatus(e.target.value as FollowStatus)}
            />
          )}
        </Field>
        <Field label="Note">
          {(c) => <Textarea {...c} rows={3} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />}
        </Field>
        {m.error ? <ErrorLine error={m.error} /> : null}
        <Button icon={<Save className="h-4 w-4" />} loading={m.isPending} disabled={!dirty} onClick={() => m.mutate()}>
          Save
        </Button>
        {row.followed_at ? <p className="text-[13px] text-subtle">Last updated {formatDateTime(row.followed_at)}</p> : null}
      </CardBody>
    </Card>
  );
}

export default function RecordDetailPage() {
  const { id = "", recordId = "" } = useParams();
  const location = useLocation();
  const handed = (location.state ?? null) as { row?: RecordRow; schema?: RecordSchema | null } | null;
  const [reveal, setReveal] = useState(false);
  const agent = useAgent(id);
  const detail = useRecordDetail(id, recordId, reveal);

  const row: RecordRow | undefined = detail.data ?? (reveal ? undefined : handed?.row);
  const schema = handed?.schema ?? agent.data?.config.record_schema ?? null;
  const fields = schema?.fields ?? [];
  const detailMissing = isApiError(detail.error) && detail.error.status === 404 && detail.error.code !== "record_not_found";
  const hasPii = fields.some((f) => f.pii);
  const back = { to: `/studio/agents/${id}?tab=records`, label: agent.data?.title ? `${agent.data.title} · Records` : "Records" };

  if (!row) {
    if (detail.isPending) return <PageLoading />;
    return (
      <PageBody>
        <PageHeader title="Record" back={back} />
        <ErrorLine error={detail.error} onRetry={() => void detail.refetch()} />
      </PageBody>
    );
  }

  const known = new Set(fields.map((f) => f.key));
  const retired = Object.keys(row.data).filter((k) => !known.has(k));

  return (
    <PageBody>
      <PageHeader
        back={back}
        title={row.visitor_name || "Visitor"}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[13px]">{row.visitor_email_masked}</span>
            <StatusBadge status={row.status} />
            <FollowBadge status={row.follow_status} />
          </span>
        }
        actions={
          hasPii && !detailMissing ? (
            <Button
              variant="outline"
              icon={reveal ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              onClick={() => setReveal((r) => !r)}
            >
              {reveal ? "Hide personal data" : "Show personal data"}
            </Button>
          ) : null
        }
      />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="min-w-0 space-y-6">
          <Card>
            <CardHeader
              title="Captured details"
              description={`Started ${formatDateTime(row.created_at)}${row.completed_at ? ` · completed ${formatDateTime(row.completed_at)}` : ""}`}
            />
            <CardBody>
              {row.summary ? <p className="mb-4 rounded-control bg-slate-50 p-3 text-sm">{row.summary}</p> : null}
              <div className="divide-y divide-border">
                {fields.map((f) => {
                  const v = row.data[f.key];
                  return (
                    <div key={f.key} className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-3 py-2.5 text-sm">
                      <div className="text-muted">
                        {f.label}
                        {f.required ? <span className="text-warning"> *</span> : null}
                      </div>
                      <div className="min-w-0 break-words">
                        {formatRecordValue(f, v)}
                        {v?.evidence && v.evidence !== "•••" ? <div className="mt-0.5 text-[13px] text-subtle">“{v.evidence}”</div> : null}
                      </div>
                    </div>
                  );
                })}
                {retired.map((k) => (
                  <div key={k} className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-3 py-2.5 text-sm">
                    <div className="text-subtle">{k} (retired)</div>
                    <div className="min-w-0 break-words">{formatRecordValue(undefined, row.data[k])}</div>
                  </div>
                ))}
              </div>
            </CardBody>
          </Card>
          {!detailMissing ? <FollowCard key={`${row.id}:${row.follow_status}:${row.follow_note}`} agentId={id} row={row} /> : null}
        </div>
        <Card className="min-w-0">
          <CardHeader title="Transcript" />
          <CardBody>
            {detail.data ? (
              <Transcript messages={detail.data.transcript} />
            ) : detail.isPending ? (
              <SkeletonRows rows={4} />
            ) : detailMissing ? (
              <p className="text-sm text-muted">The transcript is not available yet.</p>
            ) : (
              <ErrorLine error={detail.error} onRetry={() => void detail.refetch()} />
            )}
          </CardBody>
        </Card>
      </div>
    </PageBody>
  );
}
