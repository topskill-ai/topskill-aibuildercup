/** Helpers to fake `fetch` responses and SSE bodies in tests. */
import { vi } from "vitest";

export function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

export function textResponse(status: number, body: string, contentType = "text/html"): Response {
  return new Response(body, { status, headers: { "Content-Type": contentType } });
}

export interface StreamControl {
  response: Response;
  push: (text: string) => void;
  close: () => void;
  fail: (err?: unknown) => void;
}

/** A 200 `text/event-stream` response whose body the test drives chunk by chunk. */
export function sseStream(signal?: AbortSignal | null): StreamControl {
  const encoder = new TextEncoder();
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctrl = c;
    },
  });
  signal?.addEventListener("abort", () => {
    try {
      ctrl.error(new DOMException("The operation was aborted.", "AbortError"));
    } catch {
      /* already closed */
    }
  });
  return {
    response: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    push: (text) => ctrl.enqueue(encoder.encode(text)),
    close: () => ctrl.close(),
    fail: (err = new TypeError("network error")) => ctrl.error(err),
  };
}

/** A 200 SSE response with a fixed list of raw chunks. */
export function sseChunks(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const chunk of chunks) c.enqueue(encoder.encode(chunk));
      c.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

export function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

export function mockFetch() {
  const fn = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fn);
  return fn;
}

/** Headers of the n-th fetch call as a plain object. */
export function callHeaders(fn: ReturnType<typeof mockFetch>, n: number): Record<string, string> {
  const init = fn.mock.calls[n]?.[1];
  return { ...((init?.headers as Record<string, string> | undefined) ?? {}) };
}
