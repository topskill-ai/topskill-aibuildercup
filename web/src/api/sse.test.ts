import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { frame, jsonResponse, mockFetch, sseChunks, sseStream, textResponse } from "@/test/http";

const firebase = vi.hoisted(() => ({ getIdToken: vi.fn<(force?: boolean) => Promise<string | null>>() }));
vi.mock("@/lib/firebase", () => firebase);
vi.mock("@/lib/devUser", () => ({ getDevUser: () => null }));

import { ApiError } from "./errors";
import { SSEParser, streamSSE } from "./sse";
import type { ChatStreamEvent } from "./types";

const STATUS = { state: "ready", conversation_id: "cnv_1", turn_id: "turn-abc-123", request_id: "turn_9f", model: "m" };
const DONE = {
  conversation_id: "cnv_1",
  message_id: "m_turn_9f",
  turn_id: "turn-abc-123",
  outcome: "ok",
  usage: null,
  billing: { charged_cents: 100, free: false, round_index: 3, free_rounds_left: 0, balance_cents: 9900 },
};
const BODY = { conversation_id: null, turn_id: "turn-abc-123", message: "hi" };

async function collect(path = "/api/public/agents/visa/chat", body: unknown = BODY, signal?: AbortSignal) {
  const events: ChatStreamEvent[] = [];
  await streamSSE<ChatStreamEvent>(path, body, { onEvent: (e) => events.push(e), signal });
  return events;
}

describe("SSEParser", () => {
  it("parses frames split across arbitrary chunk boundaries", () => {
    const text = frame("status", STATUS) + frame("text_delta", { delta: "Hel" }) + frame("text_delta", { delta: "lo" });
    const parser = new SSEParser();
    const out = [];
    for (let i = 0; i < text.length; i += 7) out.push(...parser.push(text.slice(i, i + 7)));
    out.push(...parser.flush());
    expect(out.map((f) => f.event)).toEqual(["status", "text_delta", "text_delta"]);
    expect(out[2].data).toEqual({ delta: "lo" });
  });

  it("joins multiple data lines with a newline and ignores comment lines", () => {
    const parser = new SSEParser();
    const frames = parser.push(': ping\n\nevent: text_delta\n: inline comment\ndata: {"delta":\ndata: "a\\nb"}\n\n');
    expect(frames).toEqual([{ event: "text_delta", data: { delta: "a\nb" } }]);
  });

  it("handles CRLF line endings, including a CR/LF pair split across chunks", () => {
    const parser = new SSEParser();
    const out = [
      ...parser.push('event: text_delta\r\ndata: {"delta":"x"}\r'),
      ...parser.push('\n\r\nevent: done\r\ndata: {"outcome":"ok"}\r\n\r\n'),
    ];
    expect(out).toEqual([
      { event: "text_delta", data: { delta: "x" } },
      { event: "done", data: { outcome: "ok" } },
    ]);
  });

  it("drops frames with invalid JSON or without data", () => {
    const parser = new SSEParser();
    expect(parser.push("event: status\n\nevent: x\ndata: {not json}\n\n")).toEqual([]);
  });
});

describe("streamSSE", () => {
  let fetchMock: ReturnType<typeof mockFetch>;

  beforeEach(() => {
    fetchMock = mockFetch();
    firebase.getIdToken.mockReset();
    firebase.getIdToken.mockImplementation(async (force) => (force ? "fresh-token" : "token-1"));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("delivers events in order across chunk boundaries and posts JSON", async () => {
    const all = frame("status", STATUS) + ": ping\n\n" + frame("text_delta", { delta: "Hi" }) + frame("done", DONE);
    fetchMock.mockResolvedValueOnce(sseChunks([all.slice(0, 13), all.slice(13, 80), all.slice(80)]));
    const events = await collect();
    expect(events.map((e) => e.event)).toEqual(["status", "text_delta", "done"]);
    const init = fetchMock.mock.calls[0][1]!;
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify(BODY));
    expect((init.headers as Record<string, string>).Accept).toBe("text/event-stream");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer token-1");
  });

  it("delivers done exactly once and nothing after it", async () => {
    fetchMock.mockResolvedValueOnce(
      sseChunks([frame("status", STATUS), frame("done", DONE), frame("text_delta", { delta: "late" }), frame("done", DONE)]),
    );
    const events = await collect();
    expect(events.map((e) => e.event)).toEqual(["status", "done"]);
  });

  it("synthesizes connection_lost + done(aborted) once on a mid-stream network failure", async () => {
    const stream = sseStream();
    fetchMock.mockResolvedValueOnce(stream.response);
    const pending = collect();
    stream.push(frame("status", STATUS) + frame("text_delta", { delta: "part" }));
    await new Promise((r) => setTimeout(r, 0));
    stream.fail(new TypeError("network error"));
    const events = await pending;
    expect(events.map((e) => e.event)).toEqual(["status", "text_delta", "error", "done"]);
    expect(events[2]).toEqual({ event: "error", data: { code: "connection_lost", message: "Connection lost", trace_id: "" } });
    expect(events[3]).toEqual({
      event: "done",
      data: { conversation_id: null, message_id: null, turn_id: "turn-abc-123", outcome: "aborted", usage: null, billing: null },
    });
  });

  it("synthesizes connection_lost when the server closes without done", async () => {
    fetchMock.mockResolvedValueOnce(sseChunks([frame("status", STATUS), frame("text_delta", { delta: "x" })]));
    const events = await collect();
    expect(events.map((e) => e.event)).toEqual(["status", "text_delta", "error", "done"]);
    expect(events.filter((e) => e.event === "done")).toHaveLength(1);
  });

  it("does not synthesize anything when the stream already delivered done before failing", async () => {
    const stream = sseStream();
    fetchMock.mockResolvedValueOnce(stream.response);
    const pending = collect();
    stream.push(frame("done", DONE));
    const events = await pending;
    stream.fail();
    expect(events.map((e) => e.event)).toEqual(["done"]);
  });

  it("resolves quietly when aborted mid-stream", async () => {
    const controller = new AbortController();
    const stream = sseStream(controller.signal);
    fetchMock.mockResolvedValueOnce(stream.response);
    const events: ChatStreamEvent[] = [];
    const pending = streamSSE<ChatStreamEvent>("/api/public/agents/visa/chat", BODY, {
      signal: controller.signal,
      onEvent: (e) => {
        events.push(e);
        if (e.event === "text_delta") controller.abort();
      },
    });
    stream.push(frame("status", STATUS) + frame("text_delta", { delta: "a" }));
    await expect(pending).resolves.toBeUndefined();
    expect(events.map((e) => e.event)).toEqual(["status", "text_delta"]);
  });

  it("resolves quietly when aborted before the response arrives", async () => {
    fetchMock.mockRejectedValueOnce(new DOMException("aborted", "AbortError"));
    await expect(collect()).resolves.toEqual([]);
  });

  it("retries a 401 once with a force-refreshed token before reading the stream", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { code: "unauthenticated", message: "x", trace_id: "t", detail: null }))
      .mockResolvedValueOnce(sseChunks([frame("status", STATUS), frame("done", DONE)]));
    const events = await collect();
    expect(events.map((e) => e.event)).toEqual(["status", "done"]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(firebase.getIdToken).toHaveBeenLastCalledWith(true);
    expect((fetchMock.mock.calls[1][1]!.headers as Record<string, string>).Authorization).toBe("Bearer fresh-token");
  });

  it("throws ApiError from the JSON envelope for a pre-stream error", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(409, { code: "turn_in_progress", message: "A round is already running.", trace_id: "tr-9", detail: null }),
    );
    const onEvent = vi.fn();
    const err = await streamSSE("/api/public/agents/visa/chat", BODY, { onEvent }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: "turn_in_progress", traceId: "tr-9" });
    expect(onEvent).not.toHaveBeenCalled();
  });

  it("maps a non-JSON 413 to payload_too_large and no response to network_error", async () => {
    fetchMock.mockResolvedValueOnce(textResponse(413, "too large"));
    await expect(collect()).rejects.toMatchObject({ code: "payload_too_large", status: 413 });
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(collect()).rejects.toMatchObject({ code: "network_error", status: 0 });
  });

  it("ignores unknown event names gracefully (passes them through to the caller)", async () => {
    fetchMock.mockResolvedValueOnce(sseChunks([frame("status", STATUS), frame("future_event", { a: 1 }), frame("done", DONE)]));
    const events = await collect();
    expect(events.map((e) => e.event)).toEqual(["status", "future_event", "done"]);
  });
});
