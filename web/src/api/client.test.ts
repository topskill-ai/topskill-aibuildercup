import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { callHeaders, jsonResponse, mockFetch, textResponse } from "@/test/http";

const firebase = vi.hoisted(() => ({ getIdToken: vi.fn<(force?: boolean) => Promise<string | null>>() }));
vi.mock("@/lib/firebase", () => firebase);

const dev = vi.hoisted(() => ({ user: null as null | { uid: string; role: "visitor" | "creator"; email?: string } }));
vi.mock("@/lib/devUser", () => ({ getDevUser: () => dev.user }));

import { apiFetch, apiUrl, authHeaders } from "./client";
import { ApiError, ErrorCodes, hasCode } from "./errors";

async function catchError(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (err) {
    expect(err).toBeInstanceOf(ApiError);
    return err as ApiError;
  }
  throw new Error("expected the call to throw");
}

describe("apiFetch", () => {
  let fetchMock: ReturnType<typeof mockFetch>;

  beforeEach(() => {
    fetchMock = mockFetch();
    dev.user = null;
    firebase.getIdToken.mockReset();
    firebase.getIdToken.mockImplementation(async (force) => (force ? "fresh-token" : "token-1"));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns decoded JSON and sends the Bearer token", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { uid: "u1" }));
    const me = await apiFetch<{ uid: string }>("GET", "/api/me");
    expect(me).toEqual({ uid: "u1" });
    expect(callHeaders(fetchMock, 0).Authorization).toBe("Bearer token-1");
    expect(fetchMock.mock.calls[0][0]).toBe(apiUrl("/api/me"));
  });

  it("maps the JSON error envelope to ApiError with code, trace id and detail", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(402, {
        code: "visitor_insufficient_points",
        message: "You need 1.00 credits; you have 0.40.",
        trace_id: "3f2a9c",
        detail: { required_cents: 100, balance_cents: 40 },
      }),
    );
    const err = await catchError(apiFetch("POST", "/api/public/agents/x/chat", { body: {} }));
    expect(err.status).toBe(402);
    expect(err.code).toBe(ErrorCodes.visitor_insufficient_points);
    expect(err.message).toBe("You need 1.00 credits; you have 0.40.");
    expect(err.traceId).toBe("3f2a9c");
    expect(err.detail).toEqual({ required_cents: 100, balance_cents: 40 });
    expect(hasCode(err, "visitor_insufficient_points")).toBe(true);
    expect(hasCode(err, "insufficient_points")).toBe(false);
  });

  it("maps a non-JSON 413 (Cloud Run front end) to payload_too_large", async () => {
    fetchMock.mockResolvedValueOnce(textResponse(413, "<html>Request Entity Too Large</html>"));
    const err = await catchError(apiFetch("POST", "/api/skills", { body: new FormData() }));
    expect(err.status).toBe(413);
    expect(err.code).toBe("payload_too_large");
  });

  it("maps any other non-JSON error body to internal_error and keeps the X-Trace-Id header", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response("Bad gateway", { status: 502, headers: { "Content-Type": "text/plain", "X-Trace-Id": "hdr-trace" } }),
    );
    const err = await catchError(apiFetch("GET", "/api/agents"));
    expect(err.status).toBe(502);
    expect(err.code).toBe("internal_error");
    expect(err.traceId).toBe("hdr-trace");
  });

  it("maps a failed request (no response) to network_error with status 0", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const err = await catchError(apiFetch("GET", "/api/me"));
    expect(err.status).toBe(0);
    expect(err.code).toBe("network_error");
  });

  it("re-throws AbortError unchanged", async () => {
    fetchMock.mockRejectedValueOnce(new DOMException("aborted", "AbortError"));
    await expect(apiFetch("GET", "/api/me")).rejects.toMatchObject({ name: "AbortError" });
  });

  it("on 401 forces a token refresh and retries exactly once", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { code: "unauthenticated", message: "Expired", trace_id: "t1", detail: null }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    await expect(apiFetch("GET", "/api/wallet")).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(firebase.getIdToken).toHaveBeenLastCalledWith(true);
    expect(callHeaders(fetchMock, 1).Authorization).toBe("Bearer fresh-token");
  });

  it("surfaces the second 401 as unauthenticated (no endless retries)", async () => {
    const unauth = () =>
      jsonResponse(401, { code: "unauthenticated", message: "Expired", trace_id: "t2", detail: null });
    fetchMock.mockResolvedValueOnce(unauth()).mockResolvedValueOnce(unauth());
    const err = await catchError(apiFetch("GET", "/api/wallet"));
    expect(err.code).toBe("unauthenticated");
    expect(err.traceId).toBe("t2");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not retry a 401 when no token was sent", async () => {
    firebase.getIdToken.mockResolvedValue(null);
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { code: "unauthenticated", message: "", trace_id: "t3", detail: null }));
    const err = await catchError(apiFetch("GET", "/api/me"));
    expect(err.code).toBe("unauthenticated");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(callHeaders(fetchMock, 0).Authorization).toBeUndefined();
  });

  it("sends X-Dev-* headers instead of a Bearer token in developer login", async () => {
    dev.user = { uid: "creator-ana", role: "creator", email: "ana@example.com" };
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await apiFetch("GET", "/api/me");
    const h = callHeaders(fetchMock, 0);
    expect(h["X-Dev-User"]).toBe("creator-ana");
    expect(h["X-Dev-Role"]).toBe("creator");
    expect(h["X-Dev-Email"]).toBe("ana@example.com");
    expect(h.Authorization).toBeUndefined();
    expect(firebase.getIdToken).not.toHaveBeenCalled();
  });

  it("JSON-encodes bodies, leaves FormData to the browser and drops empty query values", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await apiFetch("POST", "/api/agents/draft", { body: { skill_ids: ["skl_1"], quality: "fast" } });
    expect(callHeaders(fetchMock, 0)["Content-Type"]).toBe("application/json");
    expect(fetchMock.mock.calls[0][1]?.body).toBe('{"skill_ids":["skl_1"],"quality":"fast"}');

    const form = new FormData();
    form.append("files", new Blob(["x"]), "a.md");
    await apiFetch("POST", "/api/skills", { body: form });
    expect(callHeaders(fetchMock, 1)["Content-Type"]).toBeUndefined();
    expect(fetchMock.mock.calls[1][1]?.body).toBe(form);

    await apiFetch("GET", "/api/wallet/ledger", { query: { category: "visitor_payment", agent_id: null, cursor: "", limit: 50 } });
    expect(fetchMock.mock.calls[2][0]).toBe(apiUrl("/api/wallet/ledger") + "?category=visitor_payment&limit=50");
  });
});

describe("authHeaders", () => {
  beforeEach(() => {
    dev.user = null;
    firebase.getIdToken.mockReset();
  });

  it("is empty when signed out", async () => {
    firebase.getIdToken.mockResolvedValue(null);
    await expect(authHeaders()).resolves.toEqual({});
  });

  it("is empty (not a throw) when Firebase fails", async () => {
    firebase.getIdToken.mockRejectedValue(new Error("boom"));
    await expect(authHeaders()).resolves.toEqual({});
  });
});
