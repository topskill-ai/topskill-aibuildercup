/**
 * REST client (SPEC §11.6). Every call goes straight to `VITE_API_BASE` (Cloud Run) with
 * `Authorization: Bearer <Firebase ID token>`, or `X-Dev-*` headers when developer login is on.
 * Success returns the decoded body; failure throws `ApiError` (branch on `err.code`).
 */
import { config } from "@/lib/config";
import { getDevUser } from "@/lib/devUser";
import { getIdToken } from "@/lib/firebase";
import { errorFromResponse, networkError } from "./errors";

export type HttpMethod = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
export type QueryValue = string | number | boolean | null | undefined;

export interface ApiFetchOptions {
  /** JSON-encoded unless it is a `FormData` (sent as multipart, browser sets the boundary). */
  body?: unknown;
  /** Query parameters; `null`/`undefined`/`""` values are omitted. */
  query?: Record<string, QueryValue>;
  signal?: AbortSignal;
  /** Return the raw `Response` (2xx only) instead of decoding it — e.g. CSV downloads. */
  raw?: boolean;
  /** Extra request headers. */
  headers?: Record<string, string>;
}

/** Absolute URL for an API path (`/api/...`) plus optional query string. */
export function apiUrl(path: string, query?: Record<string, QueryValue>): string {
  const base = /^https?:\/\//i.test(path) ? path : `${config.apiBase}${path.startsWith("/") ? "" : "/"}${path}`;
  if (!query) return base;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === "") continue;
    params.append(key, String(value));
  }
  const qs = params.toString();
  if (!qs) return base;
  return `${base}${base.includes("?") ? "&" : "?"}${qs}`;
}

/**
 * Auth headers for the current viewer: `X-Dev-User`/`X-Dev-Role`/`X-Dev-Email` when developer
 * login is active (VITE_DEV_AUTH=1 and a dev user is stored), else a Bearer ID token when signed
 * in, else `{}`. `forceRefresh` forces a fresh ID token (used for the single 401 retry).
 */
export async function authHeaders(opts: { forceRefresh?: boolean } = {}): Promise<Record<string, string>> {
  const dev = getDevUser();
  if (dev) {
    const headers: Record<string, string> = { "X-Dev-User": dev.uid, "X-Dev-Role": dev.role };
    if (dev.email) headers["X-Dev-Email"] = dev.email;
    return headers;
  }
  try {
    const token = await getIdToken(Boolean(opts.forceRefresh));
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

/** True when the headers carry a Firebase token that a forced refresh could renew. */
export function canRefreshAuth(headers: Record<string, string>): boolean {
  return typeof headers.Authorization === "string";
}

export interface SendOptions {
  method: HttpMethod;
  url: string;
  body?: BodyInit | null;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

/**
 * Low-level send shared by `apiFetch` and `streamSSE`: attaches auth headers and performs the
 * single 401 retry with a force-refreshed token before any body is read. Network failures throw
 * `ApiError(0, "network_error")`; `AbortError` is re-thrown unchanged.
 */
export async function sendWithAuth(opts: SendOptions): Promise<Response> {
  const attempt = async (forceRefresh: boolean) => {
    const auth = await authHeaders({ forceRefresh });
    let res: Response;
    try {
      res = await fetch(opts.url, {
        method: opts.method,
        body: opts.body ?? undefined,
        headers: { ...(opts.headers ?? {}), ...auth },
        signal: opts.signal,
        credentials: "omit",
      });
    } catch (err) {
      if (isAbortError(err)) throw err;
      throw networkError(err);
    }
    return { res, auth };
  };

  const first = await attempt(false);
  if (first.res.status !== 401 || !canRefreshAuth(first.auth)) return first.res;
  // Drain the first body so the connection can be reused, then retry once with a fresh token.
  await first.res.text().catch(() => "");
  const second = await attempt(true);
  return second.res;
}

export function isAbortError(err: unknown): boolean {
  return (
    (err instanceof DOMException && err.name === "AbortError") ||
    (err instanceof Error && err.name === "AbortError")
  );
}

/** Throws the mapped `ApiError` for a non-2xx response. */
export async function throwForStatus(res: Response): Promise<void> {
  if (res.ok) return;
  const text = await res.text().catch(() => "");
  throw errorFromResponse(res.status, text, res.headers.get("X-Trace-Id") ?? "");
}

export async function apiFetch<T>(method: HttpMethod, path: string, opts: ApiFetchOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...(opts.headers ?? {}) };
  let body: BodyInit | null = null;
  if (opts.body instanceof FormData) {
    body = opts.body;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }

  const res = await sendWithAuth({ method, url: apiUrl(path, opts.query), body, headers, signal: opts.signal });
  await throwForStatus(res);

  if (opts.raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  const contentType = res.headers.get("Content-Type") ?? "";
  if (contentType.includes("json")) return JSON.parse(text) as T;
  return text as unknown as T;
}

/** Convenience wrappers. */
export const api = {
  get: <T>(path: string, opts?: Omit<ApiFetchOptions, "body">) => apiFetch<T>("GET", path, opts),
  post: <T>(path: string, body?: unknown, opts?: Omit<ApiFetchOptions, "body">) =>
    apiFetch<T>("POST", path, { ...opts, body: body ?? {} }),
  patch: <T>(path: string, body: unknown, opts?: Omit<ApiFetchOptions, "body">) =>
    apiFetch<T>("PATCH", path, { ...opts, body }),
  put: <T>(path: string, body: unknown, opts?: Omit<ApiFetchOptions, "body">) =>
    apiFetch<T>("PUT", path, { ...opts, body }),
  delete: <T>(path: string, opts?: Omit<ApiFetchOptions, "body">) => apiFetch<T>("DELETE", path, opts),
};

/**
 * Download an authenticated file (e.g. records CSV) and hand it to the browser as a save dialog.
 * Uses the filename from `Content-Disposition` when present.
 */
export async function downloadFile(path: string, fallbackName: string, query?: Record<string, QueryValue>): Promise<void> {
  const res = await apiFetch<Response>("GET", path, { raw: true, query, headers: { Accept: "*/*" } });
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  const filename = match ? decodeURIComponent(match[1]) : fallbackName;
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
