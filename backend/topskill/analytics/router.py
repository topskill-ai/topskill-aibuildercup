"""Analytics endpoints (SPEC §18.4), mounted under ``/api``.

* ``GET  /api/analytics/status``          creator  – feature on/off, data freshness, suggested questions
* ``POST /api/analytics/ask``             creator  – natural-language question → SQL, rows, answer, chart hint
* ``POST /api/internal/analytics/sync``   OIDC     – incremental Firestore → BigQuery sync (Cloud Scheduler)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from topskill import ratelimit
from topskill.analytics.nlq import SUGGESTED_QUESTIONS
from topskill.analytics.service import Analytics, get_analytics
from topskill.analytics.transform import ts
from topskill.analytics.warehouse import WarehouseError
from topskill.auth import CurrentUser, require_creator
from topskill.config import get_settings
from topskill.errors import ApiError, Codes

log = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])

SYNC_PATH = "/internal/analytics/sync"
ASK_BUCKET = "analytics_ask"


# --------------------------------------------------------------------------- wire models


class AnalyticsStatus(BaseModel):
    enabled: bool
    data_as_of: str | None
    suggestions: list[str]


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=300)


class AnalyticsColumn(BaseModel):
    name: str
    type: str


class ChartHint(BaseModel):
    type: Literal["bar", "line", "none"]
    x: str | None
    y: str | None


class AnalyticsAnswer(BaseModel):
    question: str
    outcome: Literal["answered", "cannot_answer", "rejected"]
    sql: str
    columns: list[AnalyticsColumn]
    rows: list[list[Any]]
    answer: str
    chart_hint: ChartHint
    truncated: bool
    bytes_processed: int
    model: str | None
    data_as_of: str | None


class SyncSource(BaseModel):
    read: int
    written: int
    watermark: str | None


class SyncResult(BaseModel):
    ran: bool
    reason: str
    duration_ms: int
    sources: dict[str, SyncSource]


def _unavailable() -> ApiError:
    return ApiError(503, Codes.SERVICE_UNAVAILABLE, "Ask your data is unavailable right now.")


# --------------------------------------------------------------------------- creator endpoints


@router.get("/analytics/status", response_model=AnalyticsStatus)
async def analytics_status(
    user: CurrentUser = Depends(require_creator), analytics: Analytics = Depends(get_analytics)
) -> AnalyticsStatus:
    as_of = await analytics.data_as_of() if analytics.enabled else None
    return AnalyticsStatus(
        enabled=analytics.enabled,
        data_as_of=ts(as_of) if as_of else None,
        suggestions=list(SUGGESTED_QUESTIONS),
    )


@router.post("/analytics/ask", response_model=AnalyticsAnswer)
async def analytics_ask(
    body: AskRequest,
    user: CurrentUser = Depends(require_creator),
    analytics: Analytics = Depends(get_analytics),
) -> dict[str, Any]:
    if not analytics.enabled:
        raise _unavailable()
    question = " ".join(body.question.split())
    if len(question) < 3:
        raise ApiError(
            400,
            Codes.VALIDATION_ERROR,
            "Please ask a question.",
            {"errors": [{"loc": ["body", "question"], "msg": "too short"}]},
        )
    await ratelimit.hit(ASK_BUCKET, user.uid, limit=get_settings().analytics_ask_per_hour, window="hour")
    refresh = await analytics.refresh_for_question()
    try:
        async with asyncio.timeout(analytics.limits.ask_timeout_s):
            result = await analytics.ask(question, user.uid)
    except WarehouseError as exc:
        log.error("analytics_ask_unavailable error=%s", str(exc)[:500])
        raise _unavailable() from exc
    except TimeoutError as exc:  # a model call (or the whole question) exceeded its budget
        log.warning("analytics_ask_timeout budget_s=%s", analytics.limits.ask_timeout_s)
        raise ApiError(
            503, Codes.SERVICE_UNAVAILABLE, "Ask your data took too long to answer. Please try again."
        ) from exc
    log.info(
        "analytics_ask outcome=%s rows=%d bytes=%d sync=%s",
        result.outcome,
        len(result.rows),
        result.bytes_processed,
        refresh.reason or "ran",
    )
    return result.to_wire(await analytics.data_as_of())


# --------------------------------------------------------------------------- scheduler endpoint


def _verify_google_id_token(token: str, audience: str) -> dict[str, Any]:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(token, google_requests.Request(), audience=audience)


async def require_sync_invoker(request: Request) -> str:
    """Cloud Scheduler calls with a Google-signed OIDC token whose email is in ``ANALYTICS_SYNC_INVOKERS``.
    In dev/test with the auth bypass on, any ``X-Dev-User`` may trigger a sync."""
    settings = get_settings()
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if not token.strip() or scheme.lower() != "bearer":
        if settings.dev_bypass_active and request.headers.get("x-dev-user"):
            return "dev"
        raise ApiError(401, Codes.UNAUTHENTICATED, "A Google-signed identity token is required.")
    audience = settings.analytics_sync_audience or settings.public_api_base
    try:
        claims = await asyncio.to_thread(_verify_google_id_token, token.strip(), audience)
    except Exception as exc:
        log.warning("analytics_sync_token_rejected error=%s", type(exc).__name__)
        raise ApiError(401, Codes.UNAUTHENTICATED, "The identity token is invalid.") from exc
    email = str(claims.get("email") or "")
    if not claims.get("email_verified") or email not in settings.analytics_sync_invokers:
        raise ApiError(403, Codes.FORBIDDEN, "This caller may not run the analytics sync.")
    return email


@router.post(SYNC_PATH, response_model=SyncResult)
async def analytics_sync(
    full: bool = Query(False, description="Ignore watermarks and copy everything again (backfill)."),
    invoker: str = Depends(require_sync_invoker),
    analytics: Analytics = Depends(get_analytics),
) -> dict[str, Any]:
    if not analytics.enabled:
        raise _unavailable()
    try:
        report = await analytics.sync(full=full)
    except WarehouseError as exc:
        log.error("analytics_sync_failed invoker=%s error=%s", invoker, str(exc)[:500])
        raise _unavailable() from exc
    return report.to_wire()
