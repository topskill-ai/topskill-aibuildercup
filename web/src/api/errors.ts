/**
 * API error model (SPEC §6.1, §6.2). UI code branches on `code` only — never on `message`.
 */

export const ErrorCodes = {
  // 400
  validation_error: "validation_error",
  slug_invalid: "slug_invalid",
  slug_reserved: "slug_reserved",
  slug_taken: "slug_taken",
  skill_invalid: "skill_invalid",
  unsupported_file_type: "unsupported_file_type",
  pricing_invalid: "pricing_invalid",
  model_not_allowed: "model_not_allowed",
  record_schema_invalid: "record_schema_invalid",
  conversation_limit_reached: "conversation_limit_reached",
  knowledge_limit_reached: "knowledge_limit_reached",
  // 401
  unauthenticated: "unauthenticated",
  // 402
  insufficient_points: "insufficient_points",
  visitor_insufficient_points: "visitor_insufficient_points",
  payer_insufficient_points: "payer_insufficient_points",
  mandate_missing: "mandate_missing",
  mandate_invalid: "mandate_invalid",
  mandate_expired: "mandate_expired",
  mandate_revoked: "mandate_revoked",
  mandate_audience: "mandate_audience",
  mandate_exceeds_max: "mandate_exceeds_max",
  mandate_budget_exhausted: "mandate_budget_exhausted",
  mandate_replay: "mandate_replay",
  // 403
  creator_required: "creator_required",
  forbidden: "forbidden",
  // 404
  not_found: "not_found",
  agent_not_found: "agent_not_found",
  skill_not_found: "skill_not_found",
  record_not_found: "record_not_found",
  session_not_owned: "session_not_owned",
  // 409
  turn_in_progress: "turn_in_progress",
  record_field_type_locked: "record_field_type_locked",
  // 410
  agent_offline: "agent_offline",
  // 413
  payload_too_large: "payload_too_large",
  // 429
  rate_limited: "rate_limited",
  // 500 / 502 / 503
  internal_error: "internal_error",
  upstream_error: "upstream_error",
  draft_failed: "draft_failed",
  image_failed: "image_failed",
  service_unavailable: "service_unavailable",
  // Mid-stream SSE `error` codes (§7.4)
  turn_timeout: "turn_timeout",
  agent_run_failed: "agent_run_failed",
  // Synthesized by the client (§7.6, §11.6)
  connection_lost: "connection_lost",
  network_error: "network_error",
  // Enterprise Assistant `a2a_result.error_code` (§7.5)
  call_limit: "call_limit",
  // P2 guard (SPEC §18 "Guard"): mid-stream SSE error, A2A JSON-RPC data.code, a2a_result.error_code
  blocked_by_policy: "blocked_by_policy",
} as const;

export type ErrorCode = (typeof ErrorCodes)[keyof typeof ErrorCodes];

/** Every `mandate_*` code, for UI that treats them as one family. */
export const MANDATE_ERROR_CODES: readonly string[] = [
  ErrorCodes.mandate_missing,
  ErrorCodes.mandate_invalid,
  ErrorCodes.mandate_expired,
  ErrorCodes.mandate_revoked,
  ErrorCodes.mandate_audience,
  ErrorCodes.mandate_exceeds_max,
  ErrorCodes.mandate_budget_exhausted,
  ErrorCodes.mandate_replay,
];

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly traceId: string;
  readonly detail: Record<string, unknown> | null;

  constructor(
    status: number,
    code: string,
    message: string,
    traceId = "",
    detail: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.traceId = traceId;
    this.detail = detail;
  }
}

export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError;
}

/** True when `err` is an ApiError carrying `code` (or any of `codes`). */
export function hasCode(err: unknown, ...codes: string[]): err is ApiError {
  return isApiError(err) && codes.includes(err.code);
}

/** User-facing message for any thrown value. */
export function errorMessage(err: unknown): string {
  if (isApiError(err)) return err.message || "Something went wrong.";
  if (err instanceof Error && err.message) return err.message;
  return "Something went wrong.";
}

/** Trace id of an ApiError, or "" for anything else. */
export function errorTraceId(err: unknown): string {
  return isApiError(err) ? err.traceId : "";
}

const FALLBACK_MESSAGES: Record<string, string> = {
  payload_too_large: "The upload is too large.",
  internal_error: "Something went wrong on our side. Please try again.",
  network_error: "Could not reach the server. Check your connection and try again.",
  unauthenticated: "Please sign in to continue.",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Build an ApiError from a non-2xx response whose body has already been read as text.
 * A JSON envelope maps field by field; anything else (e.g. Cloud Run's front-end 413) maps to
 * `payload_too_large` for 413 and `internal_error` otherwise.
 */
export function errorFromResponse(status: number, bodyText: string, headerTraceId = ""): ApiError {
  let parsed: unknown = null;
  try {
    parsed = bodyText ? JSON.parse(bodyText) : null;
  } catch {
    parsed = null;
  }
  if (isRecord(parsed) && typeof parsed.code === "string") {
    const message = typeof parsed.message === "string" ? parsed.message : "";
    const traceId = typeof parsed.trace_id === "string" ? parsed.trace_id : headerTraceId;
    const detail = isRecord(parsed.detail) ? parsed.detail : null;
    return new ApiError(status, parsed.code, message || FALLBACK_MESSAGES[parsed.code] || "Request failed.", traceId, detail);
  }
  const code = status === 413 ? ErrorCodes.payload_too_large : ErrorCodes.internal_error;
  return new ApiError(status, code, FALLBACK_MESSAGES[code], headerTraceId, null);
}

export function networkError(cause?: unknown): ApiError {
  const err = new ApiError(0, ErrorCodes.network_error, FALLBACK_MESSAGES.network_error);
  if (cause !== undefined) (err as Error & { cause?: unknown }).cause = cause;
  return err;
}
