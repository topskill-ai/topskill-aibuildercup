/**
 * Validate a `?next=` redirect target: only same-origin absolute paths are allowed
 * ("/studio", "/a/visa-advisor?x=1"); anything else ("//evil.com", "https://…", "javascript:") → fallback.
 */
export function safeNextPath(next: string | null | undefined, fallback = "/"): string {
  if (!next) return fallback;
  if (!next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return fallback;
  for (let i = 0; i < next.length; i += 1) if (next.charCodeAt(i) < 0x20) return fallback;
  return next;
}

/** `/login?next=<current path>` for the given location. */
export function loginPath(pathname: string, search = "", hash = ""): string {
  const next = `${pathname}${search}${hash}`;
  return next && next !== "/" ? `/login?next=${encodeURIComponent(next)}` : "/login";
}
