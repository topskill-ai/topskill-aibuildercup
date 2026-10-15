/**
 * Sequential knowledge uploads (SPEC §6.1, §11.4): the web client sends exactly one file per
 * request, in order, and shows per-file progress. Pure state helpers plus a small runner so the
 * wizard and the Knowledge tab share the same behaviour.
 */
import { errorMessage, errorTraceId, isApiError } from "@/api/errors";
import { knowledgeFileProblem } from "@/features/publish/files";

export type UploadItemState = "queued" | "uploading" | "done" | "failed";

export interface UploadItem {
  /** Stable id for React keys (name + size + index). */
  id: string;
  name: string;
  size: number;
  state: UploadItemState;
  error: string | null;
  traceId: string;
}

export interface UploadProgress {
  total: number;
  done: number;
  failed: number;
  running: boolean;
}

export function queueFromFiles(files: File[]): UploadItem[] {
  return files.map((f, i) => {
    const problem = knowledgeFileProblem(f);
    return {
      id: `${i}:${f.name}:${f.size}`,
      name: f.name,
      size: f.size,
      state: problem ? "failed" : "queued",
      error: problem,
      traceId: "",
    };
  });
}

export function updateItem(items: UploadItem[], id: string, patch: Partial<UploadItem>): UploadItem[] {
  return items.map((it) => (it.id === id ? { ...it, ...patch } : it));
}

export function progressOf(items: UploadItem[]): UploadProgress {
  return {
    total: items.length,
    done: items.filter((i) => i.state === "done").length,
    failed: items.filter((i) => i.state === "failed").length,
    running: items.some((i) => i.state === "uploading" || i.state === "queued"),
  };
}

/** "Uploading 2 of 3 files…" / "3 files uploaded" / "2 of 3 files uploaded, 1 failed". */
export function progressLabel(p: UploadProgress): string {
  if (p.total === 0) return "";
  const noun = p.total === 1 ? "file" : "files";
  if (p.running) return `Uploading ${Math.min(p.done + p.failed + 1, p.total)} of ${p.total} ${noun}…`;
  if (p.failed === 0) return `${p.total} ${noun} uploaded`;
  return `${p.done} of ${p.total} ${noun} uploaded, ${p.failed} failed`;
}

export const UPLOAD_STOPPED = "Upload stopped.";

/**
 * Upload `files` one at a time with `uploadOne`, reporting every state change through `onChange`.
 * Never throws; failures are recorded per item (other files still upload). Once `signal` aborts,
 * the remaining files are marked as stopped and nothing more is sent. Returns the final items.
 */
export async function runUploads(
  files: File[],
  uploadOne: (file: File) => Promise<unknown>,
  onChange: (items: UploadItem[]) => void,
  signal?: AbortSignal,
): Promise<UploadItem[]> {
  let items = queueFromFiles(files);
  onChange(items);
  for (let i = 0; i < files.length; i += 1) {
    const item = items[i];
    if (item.state !== "queued") continue;
    if (signal?.aborted) {
      items = items.map((it) =>
        it.state === "queued" || it.state === "uploading" ? { ...it, state: "failed", error: UPLOAD_STOPPED } : it,
      );
      onChange(items);
      break;
    }
    items = updateItem(items, item.id, { state: "uploading" });
    onChange(items);
    try {
      await uploadOne(files[i]);
      items = updateItem(items, item.id, { state: "done" });
    } catch (err) {
      items = updateItem(items, item.id, {
        state: "failed",
        error: signal?.aborted
          ? UPLOAD_STOPPED
          : isApiError(err)
            ? errorMessage(err)
            : `${files[i].name} could not be uploaded.`,
        traceId: errorTraceId(err),
      });
    }
    onChange(items);
  }
  return items;
}
