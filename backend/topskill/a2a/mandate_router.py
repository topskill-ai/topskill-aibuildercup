"""Mandate endpoints (SPEC §6.4 "Mandates"): issue, list, revoke, calls, and the public JWKS."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from google.cloud import firestore
from pydantic import BaseModel, Field, field_validator

from topskill.auth import CurrentUser, get_current_user
from topskill.config import get_settings
from topskill.db import Col, get_db, new_id, utcnow
from topskill.domain import iso_z
from topskill.errors import ApiError, Codes
from topskill.pagination import clamp_limit, fetch_page

from .mandates import jwks, sign_mandate

router = APIRouter(tags=["mandates"])

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$")
CALLS_LIMIT = 100


class CreateMandateRequest(BaseModel):
    audience: str = Field(min_length=1, max_length=60)
    intent: str = Field(min_length=1, max_length=200)
    max_amount_cents: int = Field(ge=1, le=100_000)
    budget_cents: int = Field(ge=1)
    ttl_minutes: int = Field(ge=1)
    payer_agent: str | None = Field(None, max_length=60)

    @field_validator("audience")
    @classmethod
    def _audience(cls, value: str) -> str:
        value = value.strip()
        if value != "*" and not SLUG_RE.match(value):
            raise ValueError('audience must be "*" or an agent slug')
        return value

    @field_validator("intent")
    @classmethod
    def _intent(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("intent must not be blank")
        return value

    @field_validator("payer_agent")
    @classmethod
    def _payer_agent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class MandateOut(BaseModel):
    id: str
    audience: str
    intent: str
    payer_agent: str | None
    max_amount_cents: int
    budget_cents: int
    spent_cents: int
    calls: int
    status: Literal["active", "revoked", "expired"]
    issued_at: str
    expires_at: str


class CreateMandateResponse(BaseModel):
    mandate: MandateOut
    token: str


class MandatePage(BaseModel):
    items: list[MandateOut]
    next_cursor: str | None


class A2ACallOut(BaseModel):
    id: str
    mandate_id: str
    agent_slug: str
    agent_title: str
    status: Literal["running", "completed", "failed"]
    price_cents: int
    charged_cents: int
    creator_share_cents: int
    request_preview: str
    answer_preview: str
    created_at: str
    finished_at: str | None


class A2ACallList(BaseModel):
    items: list[A2ACallOut]


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromtimestamp(0, tz=UTC)


def mandate_to_wire(data: dict, *, now: datetime | None = None) -> MandateOut:
    """``mandates/{id}`` → TS ``Mandate`` (``status`` is ``expired`` once ``expires_at`` passed)."""
    now = now or utcnow()
    expires_at = _as_utc(data.get("expires_at"))
    status = data.get("status", "active")
    if status != "revoked" and expires_at < now:
        status = "expired"
    return MandateOut(
        id=data["id"],
        audience=data.get("audience", "*"),
        intent=data.get("intent", ""),
        payer_agent=data.get("payer_agent"),
        max_amount_cents=int(data.get("max_amount_cents", 0)),
        budget_cents=int(data.get("budget_cents", 0)),
        spent_cents=int(data.get("spent_cents", 0)),
        calls=int(data.get("calls", 0)),
        status=status,
        issued_at=iso_z(_as_utc(data.get("issued_at"))) or "",
        expires_at=iso_z(expires_at) or "",
    )


def call_to_wire(data: dict) -> A2ACallOut:
    """``a2a_calls/{id}`` → TS ``A2ACall``."""
    finished = data.get("finished_at")
    return A2ACallOut(
        id=data["id"],
        mandate_id=data.get("mandate_id", ""),
        agent_slug=data.get("agent_slug", ""),
        agent_title=data.get("agent_title", ""),
        status=data.get("status", "running"),
        price_cents=int(data.get("price_cents", 0)),
        charged_cents=int(data.get("charged_cents", 0)),
        creator_share_cents=int(data.get("creator_share_cents", 0)),
        request_preview=data.get("request_preview", ""),
        answer_preview=data.get("answer_preview", ""),
        created_at=iso_z(_as_utc(data.get("created_at"))) or "",
        finished_at=iso_z(_as_utc(finished)) if finished else None,
    )


def _invalid(loc: str, msg: str) -> ApiError:
    return ApiError(
        400,
        Codes.VALIDATION_ERROR,
        "The request is invalid.",
        {"errors": [{"loc": ["body", loc], "msg": msg}]},
    )


async def _own_mandate(mandate_id: str, user: CurrentUser) -> dict:
    if not mandate_id or "/" in mandate_id:
        raise ApiError(404, Codes.NOT_FOUND, "Mandate not found.")
    snap = await get_db().collection(Col.MANDATES).document(mandate_id).get()
    if not snap.exists:
        raise ApiError(404, Codes.NOT_FOUND, "Mandate not found.")
    data = snap.to_dict() or {}
    if data.get("payer_uid") != user.uid:
        raise ApiError(403, Codes.FORBIDDEN, "This mandate belongs to another account.")
    return data


@router.post("/api/mandates", status_code=201, response_model=CreateMandateResponse)
async def create_mandate(
    body: CreateMandateRequest, user: CurrentUser = Depends(get_current_user)
) -> CreateMandateResponse:
    settings = get_settings()
    if body.budget_cents < body.max_amount_cents:
        raise _invalid("budget_cents", "budget_cents must be at least max_amount_cents")
    if body.budget_cents > settings.mandate_max_budget_cents:
        raise _invalid("budget_cents", f"budget_cents must be at most {settings.mandate_max_budget_cents}")
    if body.ttl_minutes > settings.mandate_max_ttl_minutes:
        raise _invalid("ttl_minutes", f"ttl_minutes must be at most {settings.mandate_max_ttl_minutes}")
    # Second precision: the token carries integer timestamps, the doc must agree with it.
    now = utcnow().replace(microsecond=0)
    expires_at = now + timedelta(minutes=body.ttl_minutes)
    mandate_id = new_id("mdt")
    doc = {
        "id": mandate_id,
        "payer_uid": user.uid,
        "audience": body.audience,
        "intent": body.intent,
        "payer_agent": body.payer_agent,
        "max_amount_cents": body.max_amount_cents,
        "budget_cents": body.budget_cents,
        "spent_cents": 0,
        "calls": 0,
        "reserved_cents": 0,
        "status": "active",
        "kid": settings.mandate_kid,
        "issued_at": now,
        "expires_at": expires_at,
    }
    # The doc exists before the token does, so a token never points at a missing mandate.
    await get_db().collection(Col.MANDATES).document(mandate_id).create(doc)
    token = sign_mandate(
        mandate_id=mandate_id,
        payer_uid=user.uid,
        audience=body.audience,
        intent=body.intent,
        max_amount_cents=body.max_amount_cents,
        budget_cents=body.budget_cents,
        expires_at=expires_at,
        payer_agent=body.payer_agent,
        issued_at=now,
    )
    return CreateMandateResponse(mandate=mandate_to_wire(doc, now=now), token=token)


@router.get("/api/mandates", response_model=MandatePage)
async def list_mandates(
    cursor: str | None = Query(None),
    limit: int | None = Query(None, ge=1),
    user: CurrentUser = Depends(get_current_user),
) -> MandatePage:
    col = get_db().collection(Col.MANDATES)
    now = utcnow()
    items, next_cursor = await fetch_page(
        col.where(filter=firestore.FieldFilter("payer_uid", "==", user.uid)),
        collection=col,
        order_field="issued_at",
        cursor=cursor,
        limit=clamp_limit(limit),
        parse=lambda snap: mandate_to_wire(snap.to_dict() or {}, now=now),
    )
    return MandatePage(items=items, next_cursor=next_cursor)


@router.post("/api/mandates/{mandate_id}/revoke", response_model=MandateOut)
async def revoke_mandate(mandate_id: str, user: CurrentUser = Depends(get_current_user)) -> MandateOut:
    data = await _own_mandate(mandate_id, user)
    if data.get("status") != "revoked":
        await get_db().collection(Col.MANDATES).document(mandate_id).update({"status": "revoked"})
        data["status"] = "revoked"
    return mandate_to_wire(data)


@router.get("/api/mandates/{mandate_id}/calls", response_model=A2ACallList)
async def list_mandate_calls(mandate_id: str, user: CurrentUser = Depends(get_current_user)) -> A2ACallList:
    await _own_mandate(mandate_id, user)
    query = (
        get_db()
        .collection(Col.A2A_CALLS)
        .where(filter=firestore.FieldFilter("mandate_id", "==", mandate_id))
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(CALLS_LIMIT)
    )
    return A2ACallList(items=[call_to_wire(snap.to_dict() or {}) async for snap in query.stream()])


@router.get("/.well-known/jwks.json")
async def get_jwks() -> dict:
    return jwks()
