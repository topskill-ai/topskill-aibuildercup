import { AlertCircle, CheckCircle2, Clock, Loader2 } from "lucide-react";
import { formatBytes } from "@/lib/format";
import { progressLabel, progressOf, type UploadItem } from "./uploadQueue";

/** Per-file progress of a sequential knowledge upload. */
export function UploadList({ items }: { items: UploadItem[] }) {
  if (items.length === 0) return null;
  const p = progressOf(items);
  const pct = p.total === 0 ? 0 : Math.round(((p.done + p.failed) / p.total) * 100);
  return (
    <div className="space-y-2" aria-live="polite">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="font-medium">{progressLabel(p)}</span>
        <span className="font-mono text-xs text-subtle">{pct}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-chip bg-mist" aria-hidden>
        <div className="h-full rounded-chip bg-primary transition-[width] duration-300" style={{ width: `${pct}%` }} />
      </div>
      <ul className="divide-y divide-border rounded-card border border-border">
        {items.map((it) => (
          <li key={it.id} className="flex items-start gap-3 px-4 py-2.5 text-sm">
            <span className="mt-0.5 shrink-0">
              {it.state === "done" ? (
                <CheckCircle2 aria-label="Uploaded" className="h-4 w-4 text-success-text" />
              ) : it.state === "failed" ? (
                <AlertCircle aria-label="Failed" className="h-4 w-4 text-danger-text" />
              ) : it.state === "uploading" ? (
                <Loader2 aria-label="Uploading" className="h-4 w-4 animate-spin text-primary" />
              ) : (
                <Clock aria-label="Queued" className="h-4 w-4 text-subtle" />
              )}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate">{it.name}</span>
              {it.error ? (
                <span className="block text-[13px] text-danger-text">
                  {it.error}
                  {it.traceId ? <span className="ml-1 font-mono text-2xs text-subtle">Trace ID: {it.traceId}</span> : null}
                </span>
              ) : null}
            </span>
            <span className="shrink-0 font-mono text-xs text-subtle">{formatBytes(it.size)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
