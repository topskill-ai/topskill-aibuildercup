/**
 * Fetch-based SSE client (SPEC §7.1, §7.6). Used as `streamSSE<ChatStreamEvent>(...)` and
 * `streamSSE<AssistantStreamEvent>(...)`.
 *
 * Guarantees to the caller:
 * - Errors before the stream opens throw `ApiError` (JSON envelope, non-JSON 413 →
 *   `payload_too_large`, no response → `network_error`). A 401 is retried once with a
 *   force-refreshed ID token before any byte is read.
 * - Once streaming, `done` is delivered exactly once. If the connection drops (or the server
 *   closes without `done`), the client synthesizes `error{code:"connection_lost"}` followed by
 *   `done{outcome:"aborted", ...nulls}`. The server may still have settled the round, so callers
 *   refetch billing/conversation after a `connection_lost`. Synthesized `done` frames carry
 *   nulls only (no `mandate` on the assistant stream).
 * - Aborting via `signal` resolves quietly without further events.
 */
import { apiUrl, isAbortError, sendWithAuth, throwForStatus } from "./client";
import { ErrorCodes } from "./errors";
import type { DoneData, StreamErrorData } from "./types";

export interface SSEFrame {
  event: string;
  data: unknown;
}

export interface StreamSSEOptions<E> {
  onEvent: (event: E) => void;
  signal?: AbortSignal;
}

/**
 * Incremental parser for `text/event-stream`. Feed decoded text chunks; complete frames are
 * returned. Frames are separated by a blank line; lines starting with `:` are comments;
 * multiple `data:` lines are joined with `\n`; each frame's data is JSON-parsed.
 */
export class SSEParser {
  private buffer = "";

  push(chunk: string): SSEFrame[] {
    // Normalise CRLF / CR line endings so frame splitting only needs "\n\n". A trailing "\r"
    // is kept until the next chunk shows whether it starts a "\r\n" pair.
    this.buffer = (this.buffer + chunk).replace(/\r\n/g, "\n").replace(/\r(?=[\s\S])/g, "\n");
    const frames: SSEFrame[] = [];
    let idx = this.buffer.indexOf("\n\n");
    while (idx !== -1) {
      const raw = this.buffer.slice(0, idx);
      this.buffer = this.buffer.slice(idx + 2);
      const frame = parseFrame(raw);
      if (frame) frames.push(frame);
      idx = this.buffer.indexOf("\n\n");
    }
    return frames;
  }

  /** Parse whatever is left once the stream ended (a final frame without a trailing blank line). */
  flush(): SSEFrame[] {
    const rest = this.buffer.replace(/\r\n?/g, "\n");
    this.buffer = "";
    if (!rest.trim()) return [];
    const frame = parseFrame(rest);
    return frame ? [frame] : [];
  }
}

function parseFrame(raw: string): SSEFrame | null {
  let event = "message";
  const data: string[] = [];
  for (const line of raw.split("\n")) {
    if (line === "" || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
    // `id:` and `retry:` are not used by this protocol.
  }
  if (data.length === 0) return null;
  try {
    return { event, data: JSON.parse(data.join("\n")) };
  } catch {
    if (import.meta.env.DEV) console.warn("[sse] dropped a frame with invalid JSON", { event });
    return null;
  }
}

function connectionLostEvents(turnId: string): [SSEFrame, SSEFrame] {
  const error: StreamErrorData = { code: ErrorCodes.connection_lost, message: "Connection lost", trace_id: "" };
  const done: DoneData = {
    conversation_id: null,
    message_id: null,
    turn_id: turnId,
    outcome: "aborted",
    usage: null,
    billing: null,
  };
  return [
    { event: "error", data: error },
    { event: "done", data: done },
  ];
}

export async function streamSSE<E extends { event: string; data: unknown }>(
  path: string,
  body: unknown,
  { onEvent, signal }: StreamSSEOptions<E>,
): Promise<void> {
  let res: Response;
  try {
    res = await sendWithAuth({
      method: "POST",
      url: apiUrl(path),
      body: JSON.stringify(body ?? {}),
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      signal,
    });
  } catch (err) {
    if (isAbortError(err)) return;
    throw err;
  }
  await throwForStatus(res);

  const turnId =
    body && typeof body === "object" && typeof (body as { turn_id?: unknown }).turn_id === "string"
      ? (body as { turn_id: string }).turn_id
      : "";

  let doneSeen = false;
  const emit = (frame: SSEFrame) => {
    if (doneSeen) return; // nothing is delivered after `done`
    if (frame.event === "done") doneSeen = true;
    onEvent(frame as E);
  };
  const emitConnectionLost = () => {
    if (doneSeen) return;
    for (const frame of connectionLostEvents(turnId)) emit(frame);
  };

  if (!res.body) {
    emitConnectionLost();
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SSEParser();
  try {
    for (;;) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (err) {
        if (signal?.aborted || isAbortError(err)) return;
        emitConnectionLost();
        return;
      }
      if (chunk.done) break;
      for (const frame of parser.push(decoder.decode(chunk.value, { stream: true }))) emit(frame);
      if (doneSeen) return;
    }
    for (const frame of parser.push(decoder.decode())) emit(frame);
    for (const frame of parser.flush()) emit(frame);
    if (!doneSeen) {
      if (signal?.aborted) return;
      emitConnectionLost();
    }
  } finally {
    reader.cancel().catch(() => undefined);
  }
}
