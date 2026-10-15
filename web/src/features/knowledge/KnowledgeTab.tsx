/**
 * Knowledge tab: upload documents one per request with progress, list with status polling,
 * delete, and a test search console (SPEC §11.4).
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FilePlus2, FileText, Search, Trash2 } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { isApiError } from "@/api/errors";
import { queryKeys } from "@/api/queryClient";
import type { Agent, KnowledgeFile, Passage } from "@/api/types";
import { Badge, type BadgeVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Input } from "@/components/ui/Input";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { cn } from "@/lib/cn";
import { formatBytes, formatRelative } from "@/lib/format";
import { toast, toastApiError } from "@/lib/toast";
import { useBeforeUnload } from "@/features/studio/hooks";
import { deleteKnowledgeFile, searchKnowledge, uploadKnowledgeFile, useKnowledge } from "./api";
import { UploadList } from "./UploadList";
import { progressOf, runUploads, type UploadItem } from "./uploadQueue";

const STATUS: Record<KnowledgeFile["status"], { label: string; variant: BadgeVariant }> = {
  processing: { label: "Processing", variant: "warning" },
  ready: { label: "Ready", variant: "success" },
  failed: { label: "Failed", variant: "danger" },
  unreadable: { label: "No text found", variant: "danger" },
};

function pagesLabel(p: Passage): string {
  if (p.page_start == null) return "";
  return p.page_end != null && p.page_end !== p.page_start ? `p. ${p.page_start}–${p.page_end}` : `p. ${p.page_start}`;
}

function SearchConsole({ agentId }: { agentId: string }) {
  const [query, setQuery] = useState("");
  const m = useMutation({ mutationFn: (q: string) => searchKnowledge(agentId, q) });
  const missing = isApiError(m.error) && m.error.status === 404 && m.error.code !== "agent_not_found";
  if (missing) return null;
  return (
    <Card>
      <CardHeader title="Test search" description="See which passages the agent retrieves for a question." />
      <CardBody className="space-y-4">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (query.trim()) m.mutate(query.trim());
          }}
        >
          <Input
            aria-label="Search query"
            placeholder="e.g. work visa fees"
            value={query}
            maxLength={500}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Button type="submit" variant="outline" icon={<Search className="h-4 w-4" />} loading={m.isPending}>
            Search
          </Button>
        </form>
        {m.error ? <ErrorLine error={m.error} /> : null}
        {m.data ? (
          m.data.items.length === 0 ? (
            <p className="text-sm text-muted">No passages matched. Ready documents are searched; processing ones are not yet.</p>
          ) : (
            <ol className="space-y-3">
              {m.data.items.map((p) => (
                <li key={`${p.n}:${p.file_id}`} className="rounded-control border border-border p-3 text-sm">
                  <div className="mb-1 flex flex-wrap items-center gap-2 text-[13px]">
                    <span className="rounded-chip bg-primary-soft px-2 font-mono text-xs text-primary">[{p.n}]</span>
                    <span className="font-medium">{p.filename}</span>
                    <span className="text-subtle">{pagesLabel(p)}</span>
                  </div>
                  <p className="line-clamp-4 text-muted">{p.text}</p>
                </li>
              ))}
            </ol>
          )
        ) : null}
      </CardBody>
    </Card>
  );
}

export function KnowledgeTab({ agent }: { agent: Agent }) {
  const qc = useQueryClient();
  const list = useKnowledge(agent.id);
  const input = useRef<HTMLInputElement | null>(null);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [running, setRunning] = useState(false);
  const [over, setOver] = useState(false);
  const [toDelete, setToDelete] = useState<KnowledgeFile | null>(null);
  useBeforeUnload(running);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: queryKeys.knowledge(agent.id) });
    void qc.invalidateQueries({ queryKey: queryKeys.agent(agent.id) });
  };

  const upload = async (files: File[]) => {
    if (files.length === 0 || running) return;
    setRunning(true);
    try {
      const final = await runUploads(
        files,
        async (f) => {
          await uploadKnowledgeFile(agent.id, f);
          refresh();
        },
        setUploads,
      );
      const p = progressOf(final);
      if (p.failed === 0) toast.success(`${p.done} file${p.done === 1 ? "" : "s"} uploaded. Indexing now.`);
    } finally {
      setRunning(false);
      refresh();
    }
  };

  const del = useMutation({
    mutationFn: (f: KnowledgeFile) => deleteKnowledgeFile(agent.id, f.file_id),
    onSuccess: (_r, f) => {
      toast.success(`${f.filename} removed`);
      setToDelete(null);
      refresh();
    },
    onError: (err) => toastApiError(err, "Could not remove the file"),
  });

  const files = list.data?.items ?? [];

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Documents"
          description="The agent searches these and cites them with page numbers. Changes apply immediately."
          actions={
            <Button
              size="sm"
              icon={<FilePlus2 className="h-4 w-4" />}
              disabled={running}
              onClick={() => input.current?.click()}
            >
              Add files
            </Button>
          }
        />
        <CardBody className="space-y-4">
          <input
            ref={input}
            type="file"
            multiple
            accept=".pdf,.docx,.txt,.md,.csv"
            className="hidden"
            onChange={(e) => {
              const picked = Array.from(e.target.files ?? []);
              e.target.value = "";
              void upload(picked);
            }}
          />
          <div
            onDragOver={(e) => {
              e.preventDefault();
              if (!running) setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={(e: DragEvent<HTMLDivElement>) => {
              e.preventDefault();
              setOver(false);
              void upload(Array.from(e.dataTransfer.files ?? []));
            }}
            className={cn(
              "rounded-card border-2 border-dashed px-4 py-5 text-center text-sm text-muted transition-colors",
              over ? "border-primary bg-primary-soft" : "border-border bg-slate-50",
            )}
          >
            Drop PDF, DOCX, TXT, MD or CSV files here (10 MB each, up to 20 per agent).
          </div>
          {uploads.length > 0 ? <UploadList items={uploads} /> : null}

          {list.isPending ? (
            <SkeletonRows rows={3} />
          ) : list.error ? (
            <ErrorLine error={list.error} onRetry={() => void list.refetch()} />
          ) : files.length === 0 ? (
            <EmptyState
              icon={<FileText />}
              title="No documents yet"
              description="Add the guides and checklists your answers are based on."
            />
          ) : (
            <ul className="divide-y divide-border rounded-card border border-border" data-testid="knowledge-list">
              {files.map((f) => {
                const st = STATUS[f.status];
                return (
                  <li key={f.file_id} className="flex items-start gap-3 px-4 py-3">
                    <FileText aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-subtle" />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-medium">{f.filename}</span>
                        <Badge variant={st.variant} dot>
                          {st.label}
                        </Badge>
                      </div>
                      <div className="mt-0.5 text-[13px] text-muted">
                        {formatBytes(f.size)}
                        {f.pages != null ? ` · ${f.pages} page${f.pages === 1 ? "" : "s"}` : ""} ·{" "}
                        <span className="text-subtle">{formatRelative(f.updated_at)}</span>
                      </div>
                      {f.error ? <p className="mt-1 text-[13px] text-danger">{f.error}</p> : null}
                      {f.status === "unreadable" && !f.error ? (
                        <p className="mt-1 text-[13px] text-danger">No text could be extracted (scanned image?).</p>
                      ) : null}
                    </div>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Remove ${f.filename}`}
                      onClick={() => setToDelete(f)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
          {list.data?.backend ? (
            <p className="text-2xs text-subtle">
              Search backend: {list.data.backend === "file_search" ? "Gemini File Search" : list.data.backend}
            </p>
          ) : null}
        </CardBody>
      </Card>

      <SearchConsole agentId={agent.id} />

      <Dialog
        open={toDelete !== null}
        onOpenChange={(o) => !o && !del.isPending && setToDelete(null)}
        title="Remove this document?"
        description={toDelete ? `${toDelete.filename} is removed from the agent's knowledge right away.` : undefined}
        size="sm"
        footer={
          <>
            <Button variant="outline" disabled={del.isPending} onClick={() => setToDelete(null)}>
              Cancel
            </Button>
            <Button variant="danger" loading={del.isPending} onClick={() => toDelete && del.mutate(toDelete)}>
              Remove
            </Button>
          </>
        }
      />
    </div>
  );
}
