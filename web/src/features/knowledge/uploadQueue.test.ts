import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/errors";
import { progressLabel, progressOf, queueFromFiles, runUploads, UPLOAD_STOPPED, type UploadItem } from "./uploadQueue";

function file(name: string, size = 10): File {
  return new File([new Uint8Array(size)], name);
}

describe("knowledge upload queue", () => {
  it("rejects unsupported files up front", () => {
    const items = queueFromFiles([file("guide.pdf"), file("photo.png"), file("empty.txt", 0)]);
    expect(items.map((i) => i.state)).toEqual(["queued", "failed", "failed"]);
    expect(items[1].error).toMatch(/PDF, TXT, MD, CSV or DOCX/);
  });

  it("uploads one file per call, in order, and keeps going after a failure", async () => {
    const calls: string[] = [];
    let inFlight = 0;
    let maxInFlight = 0;
    const uploadOne = vi.fn(async (f: File) => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      calls.push(f.name);
      await Promise.resolve();
      inFlight -= 1;
      if (f.name === "b.md") throw new ApiError(400, "knowledge_limit_reached", "Too many files.", "t-1");
    });
    const snapshots: UploadItem[][] = [];
    const final = await runUploads([file("a.pdf"), file("b.md"), file("c.txt")], uploadOne, (s) => snapshots.push(s));

    expect(calls).toEqual(["a.pdf", "b.md", "c.txt"]);
    expect(maxInFlight).toBe(1);
    expect(final.map((i) => i.state)).toEqual(["done", "failed", "done"]);
    expect(final[1].error).toBe("Too many files.");
    expect(final[1].traceId).toBe("t-1");
    expect(snapshots.some((s) => s[0].state === "uploading")).toBe(true);
    expect(progressLabel(progressOf(final))).toBe("2 of 3 files uploaded, 1 failed");
  });

  it("stops sending files once the signal aborts", async () => {
    const controller = new AbortController();
    const calls: string[] = [];
    const uploadOne = vi.fn(async (f: File) => {
      calls.push(f.name);
      if (f.name === "b.md") {
        controller.abort();
        throw new DOMException("aborted", "AbortError");
      }
    });
    const final = await runUploads(
      [file("a.pdf"), file("b.md"), file("c.txt"), file("d.csv")],
      uploadOne,
      () => {},
      controller.signal,
    );
    expect(calls).toEqual(["a.pdf", "b.md"]);
    expect(final.map((i) => i.state)).toEqual(["done", "failed", "failed", "failed"]);
    expect(final.slice(1).every((i) => i.error === UPLOAD_STOPPED)).toBe(true);
    expect(progressOf(final).running).toBe(false);
  });

  it("describes progress while running", () => {
    const items = queueFromFiles([file("a.pdf"), file("b.pdf")]);
    items[0] = { ...items[0], state: "done" };
    items[1] = { ...items[1], state: "uploading" };
    expect(progressLabel(progressOf(items))).toBe("Uploading 2 of 2 files…");
    expect(progressLabel(progressOf([]))).toBe("");
  });
});
