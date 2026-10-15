/**
 * Records tab (SPEC §11.4): table with schema columns and Export CSV (MUST, with a personal-data
 * warning), status/follow filters, batch follow status and row → detail page (SHOULD).
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, ClipboardList, Download, Eye, EyeOff, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import type { Agent, FollowStatus, RecordField, RecordRow, RecordSchema } from "@/api/types";
import { Badge, type BadgeVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Select } from "@/components/ui/Select";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";
import { toast, toastApiError } from "@/lib/toast";
import { batchFollowAll, exportRecordsCsv, FOLLOW_STATUS_LABELS, useRecords, type RecordFilters } from "./api";
import { formatRecordValue, personalDataLabels, tableColumns } from "./schema";

const FOLLOW_VARIANT: Record<FollowStatus, BadgeVariant> = {
  new: "primary",
  following: "warning",
  done: "success",
  invalid: "neutral",
};

export function StatusBadge({ status }: { status: RecordRow["status"] }) {
  return status === "completed" ? (
    <Badge variant="success" dot>
      Completed
    </Badge>
  ) : (
    <Badge variant="neutral" dot>
      In progress
    </Badge>
  );
}

export function FollowBadge({ status }: { status: FollowStatus }) {
  return <Badge variant={FOLLOW_VARIANT[status]}>{FOLLOW_STATUS_LABELS[status]}</Badge>;
}

const FOLLOW_OPTIONS = (Object.keys(FOLLOW_STATUS_LABELS) as FollowStatus[]).map((k) => ({
  value: k,
  label: FOLLOW_STATUS_LABELS[k],
}));

function Cell({ field, row }: { field: RecordField; row: RecordRow }) {
  const text = formatRecordValue(field, row.data[field.key]);
  return (
    <span className={cn("block max-w-[220px] truncate", text === "—" && "text-subtle")} title={text}>
      {text}
    </span>
  );
}

export function RecordsTab({ agent, onOpenSettings }: { agent: Agent; onOpenSettings: () => void }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [filters, setFilters] = useState<RecordFilters>({ status: "", follow_status: "", reveal_pii: false });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchStatus, setBatchStatus] = useState<FollowStatus>("following");
  const [csvOpen, setCsvOpen] = useState(false);
  const q = useRecords(agent.id, filters);

  const schema: RecordSchema | null = q.data?.pages[0]?.schema ?? agent.config.record_schema ?? null;
  const rows = q.data?.pages.flatMap((p) => p.items) ?? [];
  const columns = tableColumns(schema);
  // Personal data follows the stored values too (a retired or un-flagged key stays masked and is
  // still exported), so labels seen while masked are remembered after "Show personal data".
  const [seen, setSeen] = useState<{ agentId: string; labels: string[] }>({ agentId: agent.id, labels: [] });
  const seenPii = seen.agentId === agent.id ? seen.labels : [];
  const newPii = personalDataLabels(schema, rows).filter((l) => !seenPii.includes(l));
  if (newPii.length > 0 || seen.agentId !== agent.id) setSeen({ agentId: agent.id, labels: [...seenPii, ...newPii] });
  const piiLabels = [...seenPii, ...newPii];
  const hasPii = piiLabels.length > 0 || Boolean(filters.reveal_pii);
  const filtered = Boolean(filters.status || filters.follow_status);

  const exportCsv = useMutation({
    mutationFn: () => exportRecordsCsv(agent.id, agent.slug),
    onSuccess: () => setCsvOpen(false),
    onError: (err) => toastApiError(err, "Could not export the CSV"),
  });
  const follow = useMutation({
    mutationFn: () => batchFollowAll(agent.id, [...selected], batchStatus),
    onSuccess: (r) => {
      toast.success(`${r.updated} record${r.updated === 1 ? "" : "s"} marked “${FOLLOW_STATUS_LABELS[batchStatus]}”`);
      setSelected(new Set());
      void qc.invalidateQueries({ queryKey: ["records", agent.id] });
    },
    onError: (err) => toastApiError(err, "Could not update the records"),
  });

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));
  const detailPath = (row: RecordRow) => `/studio/agents/${agent.id}/records/${encodeURIComponent(row.id)}`;
  const open = (row: RecordRow) => navigate(detailPath(row), { state: { row, schema } });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Select
          aria-label="Filter by status"
          className="w-40"
          value={filters.status ?? ""}
          options={[
            { value: "", label: "All statuses" },
            { value: "in_progress", label: "In progress" },
            { value: "completed", label: "Completed" },
          ]}
          onChange={(e) => {
            setSelected(new Set());
            setFilters((f) => ({ ...f, status: e.target.value as RecordFilters["status"] }));
          }}
        />
        <Select
          aria-label="Filter by follow-up"
          className="w-44"
          value={filters.follow_status ?? ""}
          options={[{ value: "", label: "Any follow-up" }, ...FOLLOW_OPTIONS]}
          onChange={(e) => {
            setSelected(new Set());
            setFilters((f) => ({ ...f, follow_status: e.target.value as RecordFilters["follow_status"] }));
          }}
        />
        {hasPii ? (
          <Button
            variant="ghost"
            size="sm"
            icon={filters.reveal_pii ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            onClick={() => setFilters((f) => ({ ...f, reveal_pii: !f.reveal_pii }))}
          >
            {filters.reveal_pii ? "Hide personal data" : "Show personal data"}
          </Button>
        ) : null}
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          icon={<Download className="h-4 w-4" />}
          loading={exportCsv.isPending}
          disabled={!schema && rows.length === 0}
          onClick={() => (hasPii ? setCsvOpen(true) : exportCsv.mutate())}
        >
          Export CSV
        </Button>
      </div>

      {selected.size > 0 ? (
        <div className="flex flex-wrap items-center gap-2 rounded-card border border-primary/20 bg-primary-soft px-4 py-2.5 text-sm">
          <span className="font-medium">{selected.size} selected</span>
          <span className="text-muted">Mark as</span>
          <Select
            aria-label="Follow-up status"
            className="w-40"
            value={batchStatus}
            options={FOLLOW_OPTIONS}
            onChange={(e) => setBatchStatus(e.target.value as FollowStatus)}
          />
          <Button size="sm" loading={follow.isPending} onClick={() => follow.mutate()}>
            Apply
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
            Clear
          </Button>
        </div>
      ) : null}

      <Card>
        {q.isPending ? (
          <SkeletonRows rows={4} className="p-5" />
        ) : q.error ? (
          <ErrorLine className="p-5" error={q.error} onRetry={() => void q.refetch()} />
        ) : rows.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={<ClipboardList />}
              title={!schema ? "Lead capture is off" : filtered ? "No matching records" : "No records yet"}
              description={
                !schema
                  ? "Add record fields in Settings and update the version. The agent then fills a record in every conversation."
                  : filtered
                    ? "Try another filter."
                    : "Records appear here as visitors chat and the agent saves what they tell it."
              }
              action={
                !schema ? (
                  <Button variant="outline" onClick={onOpenSettings}>
                    Open Settings
                  </Button>
                ) : undefined
              }
            />
          </div>
        ) : (
          <>
            <div className="hidden overflow-x-auto sm:block">
              <table className="w-full text-sm" data-testid="records-table">
                <thead>
                  <tr className="border-b border-border bg-slate-50 text-left text-xs text-muted">
                    <th className="w-10 px-4 py-2.5">
                      <input
                        type="checkbox"
                        aria-label="Select all"
                        checked={allSelected}
                        onChange={() => setSelected(allSelected ? new Set() : new Set(rows.map((r) => r.id)))}
                        className="h-4 w-4 accent-[var(--color-primary)]"
                      />
                    </th>
                    <th className="px-3 py-2.5 font-medium">Visitor</th>
                    <th className="px-3 py-2.5 font-medium">Status</th>
                    {columns.map((c) => (
                      <th key={c.key} className="px-3 py-2.5 font-medium">
                        {c.label}
                      </th>
                    ))}
                    <th className="px-3 py-2.5 font-medium">Follow-up</th>
                    <th className="px-3 py-2.5 font-medium">Updated</th>
                    <th className="w-8" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {rows.map((r) => (
                    <tr
                      key={r.id}
                      className={cn("cursor-pointer hover:bg-slate-50", selected.has(r.id) && "bg-primary-soft/40")}
                      onClick={() => open(r)}
                    >
                      <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          aria-label={`Select record of ${r.visitor_name}`}
                          checked={selected.has(r.id)}
                          onChange={() => toggle(r.id)}
                          className="h-4 w-4 accent-[var(--color-primary)]"
                        />
                      </td>
                      <td className="px-3 py-3">
                        <Link
                          to={detailPath(r)}
                          state={{ row: r, schema }}
                          onClick={(e) => e.stopPropagation()}
                          className="block max-w-[180px] truncate rounded-sm font-medium hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                        >
                          {r.visitor_name || "Visitor"}
                        </Link>
                        <span className="block max-w-[180px] truncate font-mono text-xs text-subtle">
                          {r.visitor_email_masked}
                        </span>
                      </td>
                      <td className="px-3 py-3">
                        <StatusBadge status={r.status} />
                      </td>
                      {columns.map((c) => (
                        <td key={c.key} className="px-3 py-3">
                          <Cell field={c} row={r} />
                        </td>
                      ))}
                      <td className="px-3 py-3">
                        <FollowBadge status={r.follow_status} />
                      </td>
                      <td className="px-3 py-3 whitespace-nowrap text-muted">{formatRelative(r.updated_at)}</td>
                      <td className="pr-3">
                        <ChevronRight aria-hidden className="h-4 w-4 text-subtle" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ul className="divide-y divide-border sm:hidden">
              {rows.map((r) => (
                <li key={r.id} className="flex items-start gap-3 px-4 py-3">
                  <input
                    type="checkbox"
                    aria-label={`Select record of ${r.visitor_name}`}
                    checked={selected.has(r.id)}
                    onChange={() => toggle(r.id)}
                    className="mt-1 h-4 w-4 accent-[var(--color-primary)]"
                  />
                  <button type="button" className="min-w-0 flex-1 text-left" onClick={() => open(r)}>
                    <span className="flex flex-wrap items-center gap-1.5">
                      <span className="truncate font-medium">{r.visitor_name || "Visitor"}</span>
                      <StatusBadge status={r.status} />
                      <FollowBadge status={r.follow_status} />
                    </span>
                    <span className="mt-1 block space-y-0.5 text-[13px]">
                      {columns.map((c) => (
                        <span key={c.key} className="block truncate">
                          <span className="text-muted">{c.label}: </span>
                          {formatRecordValue(c, r.data[c.key])}
                        </span>
                      ))}
                    </span>
                    <span className="mt-1 block text-xs text-subtle">{formatRelative(r.updated_at)}</span>
                  </button>
                </li>
              ))}
            </ul>
            {q.hasNextPage ? (
              <div className="border-t border-border p-3 text-center">
                <Button variant="ghost" size="sm" loading={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
                  Load more
                </Button>
              </div>
            ) : null}
          </>
        )}
      </Card>

      <Dialog
        open={csvOpen}
        onOpenChange={(o) => !exportCsv.isPending && setCsvOpen(o)}
        title="Export contains personal data"
        description="The CSV includes unmasked values of fields marked as personal data. Store and share it carefully."
        size="sm"
        footer={
          <>
            <Button variant="outline" disabled={exportCsv.isPending} onClick={() => setCsvOpen(false)}>
              Cancel
            </Button>
            <Button icon={<Download className="h-4 w-4" />} loading={exportCsv.isPending} onClick={() => exportCsv.mutate()}>
              Download CSV
            </Button>
          </>
        }
      >
        <div className="flex items-start gap-3 rounded-control bg-warning-soft p-3 text-sm text-warning">
          <ShieldAlert aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            Personal data fields: {piiLabels.length > 0 ? piiLabels.join(", ") : "values marked as personal data"}
          </span>
        </div>
      </Dialog>
    </div>
  );
}
