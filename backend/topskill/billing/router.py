"""Wallet & billing endpoints (SPEC §6.4).

``router`` is mounted under ``/api`` and ``public_router`` under ``/api/public`` by ``core/main.py``.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from topskill.auth import CurrentUser, get_current_user, require_creator
from topskill.billing import dashboard as dash
from topskill.billing import service
from topskill.billing.money import LedgerCategory
from topskill.domain import AccentColor, Pricing
from topskill.pagination import clamp_limit
from topskill.repo import get_owned_agent, get_public_agent

router = APIRouter(tags=["billing"])
public_router = APIRouter(tags=["billing"])


# --------------------------------------------------------------------------- wire models (§6.3)


class Wallet(BaseModel):
    balance_cents: int
    currency: Literal["credits"] = "credits"


class LedgerEntry(BaseModel):
    id: str
    request_id: str
    kind: Literal["CREDIT", "DEBIT", "ADJUST"]
    category: LedgerCategory
    amount_cents: int
    balance_after_cents: int
    unit_price_cents: int | None
    billing_mode: Literal["per_round", "a2a"] | None
    counterparty_uid: str | None
    counterparty_name: str | None
    agent_id: str | None
    agent_slug: str | None
    agent_title: str | None
    conversation_id: str | None
    mandate_id: str | None
    a2a_call_id: str | None
    round_index: int | None
    memo: str
    created_at: str


class LedgerPage(BaseModel):
    items: list[LedgerEntry]
    next_cursor: str | None


class TopupResponse(BaseModel):
    wallet: Wallet
    entry: LedgerEntry


class AgentStats(BaseModel):
    rounds: int
    paid_rounds: int
    conversations: int
    visitor_gross_cents: int
    creator_earned_cents: int
    a2a_calls: int
    a2a_gross_cents: int
    records: int
    last_activity_at: str | None
    guard: dict[str, int] = {}  # P2 guard (SPEC §18.2): TS GuardStats


class AgentSummary(BaseModel):
    id: str
    slug: str
    title: str
    tagline: str
    avatar_url: str | None
    accent_color: AccentColor
    status: Literal["active", "paused"]
    listed: bool
    pricing: Pricing
    active_version: int
    config_dirty: bool
    stats: AgentStats
    updated_at: str


class Dashboard(BaseModel):
    balance_cents: int
    earned_total_cents: int
    earned_today_cents: int
    earned_7d_cents: int
    visitor_income_cents: int
    a2a_income_cents: int
    paid_rounds: int
    a2a_calls: int
    agents: list[AgentSummary]
    recent_income: list[LedgerEntry]


class AgentEarnings(BaseModel):
    stats: AgentStats
    items: list[LedgerEntry]
    next_cursor: str | None


class BillingStatus(BaseModel):
    billing_mode: Literal["free", "per_round"]
    price_cents: int
    free_rounds: int
    is_owner: bool
    used_rounds: int
    free_rounds_left: int | None
    next_round_charge_cents: int
    balance_cents: int
    can_afford_next_round: bool
    owner_available: bool


# --------------------------------------------------------------------------- endpoints


@router.get("/wallet", response_model=Wallet)
async def get_wallet(user: CurrentUser = Depends(get_current_user)) -> dict:
    """The caller's wallet; opened with the signup grant on the first call."""
    return dash.wallet_wire(await service.get_or_open_wallet(user.uid))


@router.get("/wallet/ledger", response_model=LedgerPage)
async def list_ledger(
    category: str | None = Query(None),
    agent_id: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    items, next_cursor = await dash.ledger_page(
        user.uid, category=category, agent_id=agent_id, cursor=cursor, limit=clamp_limit(limit)
    )
    return {"items": items, "next_cursor": next_cursor}


@router.post("/wallet/demo-topup", response_model=TopupResponse)
async def demo_topup(user: CurrentUser = Depends(get_current_user)) -> dict:
    balance, doc_id, entry = await service.demo_topup(user)
    return {"wallet": dash.wallet_wire(balance), "entry": dash.ledger_wire(doc_id, entry, {})}


@router.get("/dashboard", response_model=Dashboard)
async def get_dashboard(user: CurrentUser = Depends(require_creator)) -> dict:
    return await dash.dashboard(user.uid)


@router.get("/agents/{agent_id}/earnings", response_model=AgentEarnings)
async def get_agent_earnings(
    agent_id: str,
    cursor: str | None = Query(None),
    limit: int | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    agent = await get_owned_agent(agent_id, user)
    stats, items, next_cursor = await dash.agent_earnings(agent, cursor=cursor, limit=clamp_limit(limit))
    return {"stats": stats, "items": items, "next_cursor": next_cursor}


@public_router.get("/agents/{slug}/billing", response_model=BillingStatus)
async def get_billing_status(slug: str, user: CurrentUser = Depends(get_current_user)) -> dict:
    agent = await get_public_agent(slug)
    return await service.billing_status(agent, user)
