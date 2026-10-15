"""Error envelope, error codes, trace ids and logging (SPEC §6.1, §6.2).

Every non-2xx JSON response has the shape ``{code, message, trace_id, detail}``. The UI branches on
``code`` only, so codes are part of the public contract and are listed in :class:`Codes`.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class Codes:
    """Every error code of SPEC §6.2 (HTTP envelopes plus stream/client/assistant codes)."""

    # 400
    VALIDATION_ERROR = "validation_error"
    SLUG_INVALID = "slug_invalid"
    SLUG_RESERVED = "slug_reserved"
    SLUG_TAKEN = "slug_taken"
    SKILL_INVALID = "skill_invalid"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    PRICING_INVALID = "pricing_invalid"
    MODEL_NOT_ALLOWED = "model_not_allowed"
    RECORD_SCHEMA_INVALID = "record_schema_invalid"
    CONVERSATION_LIMIT_REACHED = "conversation_limit_reached"
    KNOWLEDGE_LIMIT_REACHED = "knowledge_limit_reached"
    # 401
    UNAUTHENTICATED = "unauthenticated"
    # 402
    INSUFFICIENT_POINTS = "insufficient_points"
    VISITOR_INSUFFICIENT_POINTS = "visitor_insufficient_points"
    PAYER_INSUFFICIENT_POINTS = "payer_insufficient_points"
    MANDATE_MISSING = "mandate_missing"
    MANDATE_INVALID = "mandate_invalid"
    MANDATE_EXPIRED = "mandate_expired"
    MANDATE_REVOKED = "mandate_revoked"
    MANDATE_AUDIENCE = "mandate_audience"
    MANDATE_EXCEEDS_MAX = "mandate_exceeds_max"
    MANDATE_BUDGET_EXHAUSTED = "mandate_budget_exhausted"
    MANDATE_REPLAY = "mandate_replay"
    # 403
    CREATOR_REQUIRED = "creator_required"
    FORBIDDEN = "forbidden"
    # 404
    NOT_FOUND = "not_found"
    AGENT_NOT_FOUND = "agent_not_found"
    SKILL_NOT_FOUND = "skill_not_found"
    RECORD_NOT_FOUND = "record_not_found"
    SESSION_NOT_OWNED = "session_not_owned"
    # 409
    TURN_IN_PROGRESS = "turn_in_progress"
    RECORD_FIELD_TYPE_LOCKED = "record_field_type_locked"
    # 410
    AGENT_OFFLINE = "agent_offline"
    # 413
    PAYLOAD_TOO_LARGE = "payload_too_large"
    # 429
    RATE_LIMITED = "rate_limited"
    # 5xx
    INTERNAL_ERROR = "internal_error"
    UPSTREAM_ERROR = "upstream_error"
    DRAFT_FAILED = "draft_failed"
    IMAGE_FAILED = "image_failed"
    SERVICE_UNAVAILABLE = "service_unavailable"
    # stream / client / assistant only (never an HTTP envelope)
    TURN_TIMEOUT = "turn_timeout"
    AGENT_RUN_FAILED = "agent_run_failed"
    CONNECTION_LOST = "connection_lost"
    NETWORK_ERROR = "network_error"
    CALL_LIMIT = "call_limit"


ERROR_CODES: frozenset[str] = frozenset(
    value for key, value in vars(Codes).items() if key.isupper() and isinstance(value, str)
)

_STATUS_DEFAULT_CODES: dict[int, tuple[str, str]] = {
    400: (Codes.VALIDATION_ERROR, "The request is invalid."),
    401: (Codes.UNAUTHENTICATED, "Please sign in."),
    403: (Codes.FORBIDDEN, "You are not allowed to do this."),
    404: (Codes.NOT_FOUND, "Not found."),
    405: (Codes.NOT_FOUND, "Not found."),
    409: (Codes.VALIDATION_ERROR, "The request conflicts with the current state."),
    413: (Codes.PAYLOAD_TOO_LARGE, "The upload is too large."),
    429: (Codes.RATE_LIMITED, "Too many requests. Please try again later."),
    502: (Codes.UPSTREAM_ERROR, "An upstream service failed. Please try again."),
    503: (Codes.SERVICE_UNAVAILABLE, "The service is temporarily unavailable."),
}


class ApiError(Exception):
    """An error rendered as the JSON envelope with the given HTTP status and code."""

    def __init__(self, status: int, code: str, message: str, detail: dict | None = None) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail

    def to_body(self, trace_id: str | None = None) -> dict:
        return error_body(self.code, self.message, detail=self.detail, trace_id=trace_id)


# --------------------------------------------------------------------------- trace ids

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("topskill_trace_id", default=None)
_TRACE_RE = re.compile(r"^([0-9a-fA-F]{8,64})(?:/|;|$)")


def current_trace_id() -> str:
    """Trace id of the current request or background task ("" outside any request)."""
    return _trace_id.get() or ""


def set_trace_id(trace_id: str | None) -> contextvars.Token:
    return _trace_id.set(trace_id)


def reset_trace_id(token: contextvars.Token) -> None:
    _trace_id.reset(token)


def trace_id_from_header(value: str | None) -> str:
    """``X-Cloud-Trace-Context: TRACE_ID/SPAN_ID;o=1`` → ``TRACE_ID``; otherwise a fresh uuid4 hex."""
    if value:
        match = _TRACE_RE.match(value.strip())
        if match:
            return match.group(1).lower()
    return uuid.uuid4().hex


def error_body(code: str, message: str, *, detail: dict | None = None, trace_id: str | None = None) -> dict:
    return {
        "code": code,
        "message": message,
        "trace_id": trace_id if trace_id is not None else current_trace_id(),
        "detail": detail,
    }


def error_response(
    status: int, code: str, message: str, *, detail: dict | None = None, headers: dict | None = None
) -> JSONResponse:
    trace_id = current_trace_id()
    all_headers = {"X-Trace-Id": trace_id} if trace_id else {}
    if headers:
        all_headers.update(headers)
    return JSONResponse(
        error_body(code, message, detail=detail, trace_id=trace_id), status_code=status, headers=all_headers
    )


def _scope_header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


class TraceIdMiddleware:
    """Pure ASGI middleware: sets the trace id contextvar and ``request.state.trace_id``, adds the
    ``X-Trace-Id`` response header, and turns any unhandled exception into a 500 ``internal_error``
    envelope (so CORS headers and the trace id are present on 500s too)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        trace_id = trace_id_from_header(_scope_header(scope, b"x-cloud-trace-context"))
        scope.setdefault("state", {})["trace_id"] = trace_id
        token = _trace_id.set(trace_id)
        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = list(message.get("headers") or [])
                if not any(k.lower() == b"x-trace-id" for k, _ in headers):
                    headers.append((b"x-trace-id", trace_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            log.exception("unhandled_exception path=%s", scope.get("path"))
            if started or scope["type"] != "http":
                raise
            response = error_response(500, Codes.INTERNAL_ERROR, "Something went wrong. Please try again.")
            await response(scope, receive, send)
        finally:
            _trace_id.reset(token)


class BodySizeLimitMiddleware:
    """Rejects request bodies above ``max_bytes`` with a 413 ``payload_too_large`` envelope (SPEC §6.1)."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _scope_header(scope, b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, receive, send)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit_mb = self.max_bytes // (1024 * 1024)
        response = error_response(
            413,
            Codes.PAYLOAD_TOO_LARGE,
            f"The request is too large (limit {limit_mb} MB).",
            detail={"max_bytes": self.max_bytes},
        )
        await response(scope, receive, send)


class _BodyTooLarge(Exception):
    pass


# --------------------------------------------------------------------------- handlers


async def _api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    if exc.status >= 500:
        log.error("api_error status=%s code=%s message=%s", exc.status, exc.code, exc.message)
    return error_response(exc.status, exc.code, exc.message, detail=exc.detail)


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code, message = _STATUS_DEFAULT_CODES.get(exc.status_code, (Codes.INTERNAL_ERROR, "Request failed."))
    if isinstance(exc.detail, str) and exc.detail and exc.status_code < 500:
        message = exc.detail
    return error_response(exc.status_code, code, message, headers=getattr(exc, "headers", None))


async def _validation_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"loc": [str(part) for part in err.get("loc", ())], "msg": str(err.get("msg", ""))}
        for err in exc.errors()
    ]
    return error_response(400, Codes.VALIDATION_ERROR, "The request is invalid.", detail={"errors": errors})


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled_exception path=%s", request.url.path, exc_info=exc)
    return error_response(500, Codes.INTERNAL_ERROR, "Something went wrong. Please try again.")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(Exception, _unhandled_handler)


# --------------------------------------------------------------------------- logging


class _TraceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "trace_id", None):
            record.trace_id = current_trace_id() or "-"
        return True


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, understood by Cloud Logging."""

    def __init__(self, project: str) -> None:
        super().__init__()
        self.project = project

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat().replace("+00:00", "Z"),
        }
        trace_id = getattr(record, "trace_id", "-")
        if trace_id and trace_id != "-":
            payload["trace_id"] = trace_id
            payload["logging.googleapis.com/trace"] = f"projects/{self.project}/traces/{trace_id}"
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(*, level: str = "INFO", json_logs: bool = False, project: str = "top-skill") -> None:
    """Root logging with the trace id on every line (JSON in prod)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_TraceIdFilter())
    if json_logs:
        handler.setFormatter(_JsonFormatter(project))
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s [trace=%(trace_id)s] %(message)s")
        )
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_topskill", False):
            root.removeHandler(existing)
    handler._topskill = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level.upper())
