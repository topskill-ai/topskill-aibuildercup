"""Read side of billing: ledger pages, the creator dashboard and per-agent earnings (SPEC §6.4).

Every query here is backed by a §5.3 composite index or is single-field:
- ledger ``uid`` (+ ``category``) (+ ``agent_id``) ordered by ``created_at`` DESC — the four ledger indexes;
- ledger ``uid == X and category in [creator_income, a2a_income] order by created_at desc`` — the
  ``uid, category, created_at`` index. The dashboard income figures are summed **in memory** over this one
  streamed query (capped at ``MAX_DASHBOARD_INCOME_ROWS``): a Firestore SUM aggregation would need
  ``amount_cents`` in a composite index, which §5.3 does not have (prod rejects it with FAILED_PRECONDITION;
  the emulator does not), so this module never calls ``.sum(`` / ``.count(``;
- ``agents where owner_uid == X order by created_at desc`` — the agents owner index (``deleted`` in memory);
- ``agent_stats where owner_uid == X`` — single-field.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.async_query import AsyncQuery
from google.cloud.firestore_v1.base_document import DocumentSnapshot
from google.cloud.firestore_v1.base_query import FieldFilter

from topskill.billing import money
from topskill.billing.service import get_or_open_wallet
from topskill.config import get_settings
from topskill.db import Col, get_db, utcnow
from topskill.domain import AgentDoc, iso_z
from topskill.errors import ApiError, Codes
from topskill.pagination import fetch_page

RECENT_INCOME_LIMIT = 20
MAX_DASHBOARD_AGENTS = 100
MAX_DASHBOARD_INCOME_ROWS = 5000  # newest income rows summed in memory (ample for the demo)

STATS_FIELDS = (
    "rounds",
    "paid_rounds",
    "conversations",
    "visitor_gross_cents",
    "creator_earned_cents",
    "a2a_calls",
    "a2a_gross_cents",
    "records",
)


# --------------------------------------------------------------------------- wire helpers


def wallet_wire(balance_cents: int) -> dict[str, Any]:
    return {"balance_cents": balance_cents, "currency": "credits"}


def stats_wire(data: dict[str, Any] | None) -> dict[str, Any]:
    """``AgentStats`` (§6.3); zeros for a missing stats doc."""
    data = data or {}
    out: dict[str, Any] = {key: int(data.get(key) or 0) for key in STATS_FIELDS}
    last = data.get("last_activity_at")
    out["last_activity_at"] = iso_z(last) if isinstance(last, datetime) else None
    return out


def ledger_wire(doc_id: str, data: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    """``LedgerEntry`` (§6.3) from a stored ledger row."""
    counterparty = data.get("counterparty_uid")
    created = data.get("created_at")
    return {
        "id": doc_id,
        "request_id": data.get("request_id", ""),
        "kind": data.get("kind", "ADJUST"),
        "category": data.get("category", "adjust"),
        "amount_cents": int(data.get("amount_cents") or 0),
        "balance_after_cents": int(data.get("balance_after_cents") or 0),
        "unit_price_cents": data.get("unit_price_cents"),
        "billing_mode": data.get("billing_mode"),
        "counterparty_uid": counterparty,
        "counterparty_name": names.get(counterparty) if counterparty else None,
        "agent_id": data.get("agent_id"),
        "agent_slug": data.get("agent_slug"),
        "agent_title": data.get("agent_title"),
        "conversation_id": data.get("conversation_id"),
        "mandate_id": data.get("mandate_id"),
        "a2a_call_id": data.get("a2a_call_id"),
        "round_index": data.get("round_index"),
        "memo": data.get("memo", ""),
        "created_at": iso_z(created) if isinstance(created, datetime) else None,
    }


async def display_names(uids: Iterable[str | None]) -> dict[str, str]:
    """``uid → display_name`` in one batched read (cached per request by the caller)."""
    wanted = sorted({u for u in uids if u and "/" not in u})
    if not wanted:
        return {}
    db = get_db()
    refs = [db.collection(Col.USERS).document(u) for u in wanted]
    names: dict[str, str] = {}
    async for snap in db.get_all(refs, field_paths=["display_name"]):
        if snap.exists:
            names[snap.id] = str((snap.to_dict() or {}).get("display_name") or snap.id)
    return names


async def ledger_entries_wire(snaps: list[DocumentSnapshot]) -> list[dict[str, Any]]:
    rows = [(s.id, s.to_dict() or {}) for s in snaps]
    names = await display_names(d.get("counterparty_uid") for _, d in rows)
    return [ledger_wire(doc_id, data, names) for doc_id, data in rows]


# --------------------------------------------------------------------------- ledger page


def _ledger_query(uid: str, *, category: str | None = None, agent_id: str | None = None) -> AsyncQuery:
    q = get_db().collection(Col.LEDGER).where(filter=FieldFilter("uid", "==", uid))
    if category:
        q = q.where(filter=FieldFilter("category", "==", category))
    if agent_id:
        q = q.where(filter=FieldFilter("agent_id", "==", agent_id))
    return q


def validate_category(category: str | None) -> str | None:
    if category is None or category == "":
        return None
    if category not in money.CATEGORIES:
        raise ApiError(
            400,
            Codes.VALIDATION_ERROR,
            "Unknown ledger category.",
            {"errors": [{"loc": ["query", "category"], "msg": f"must be one of {sorted(money.CATEGORIES)}"}]},
        )
    return category


async def ledger_page(
    uid: str, *, category: str | None, agent_id: str | None, cursor: str | None, limit: int
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of the wallet ledger (newest first) → ``(LedgerEntry[], next_cursor)``."""
    category = validate_category(category)
    agent_id = agent_id or None
    if agent_id is not None and "/" in agent_id:
        return [], None
    snaps, next_cursor = await fetch_page(
        _ledger_query(uid, category=category, agent_id=agent_id),
        collection=get_db().collection(Col.LEDGER),
        cursor=cursor,
        limit=limit,
    )
    return await ledger_entries_wire(snaps), next_cursor


# --------------------------------------------------------------------------- dashboard


def _income_query(uid: str) -> AsyncQuery:
    """The creator's income rows, newest first (``uid, category, created_at`` index, §5.3)."""
    return (
        get_db()
        .collection(Col.LEDGER)
        .where(filter=FieldFilter("uid", "==", uid))
        .where(filter=FieldFilter("category", "in", list(money.INCOME_CATEGORIES)))
        .order_by("created_at", direction=firestore.Query.DESCENDING)
    )


def avatar_url(avatar_id: str | None) -> str | None:
    """Same rule as publish's ``avatar_url`` (§6.3); billing may not import publish (§3.1)."""
    if not avatar_id:
        return None
    return f"{get_settings().public_api_base}/api/public/avatars/{avatar_id}"


def agent_summary_wire(agent: AgentDoc, stats: dict[str, Any] | None) -> dict[str, Any]:
    """``AgentSummary`` (§6.3)."""
    return {
        "id": agent.id,
        "slug": agent.slug,
        "title": agent.title,
        "tagline": agent.tagline,
        "avatar_url": avatar_url(agent.avatar_id),
        "accent_color": agent.accent_color,
        "status": agent.status,
        "listed": agent.listed,
        "pricing": agent.pricing.model_dump(mode="json"),
        "active_version": agent.active_version,
        "config_dirty": agent.config_dirty,
        "stats": stats_wire(stats),
        "updated_at": iso_z(agent.updated_at),
    }


async def _owner_stats(uid: str) -> dict[str, dict[str, Any]]:
    q = get_db().collection(Col.AGENT_STATS).where(filter=FieldFilter("owner_uid", "==", uid))
    return {snap.id: (snap.to_dict() or {}) async for snap in q.stream()}


async def _owner_agents(uid: str) -> list[AgentDoc]:
    q = (
        get_db()
        .collection(Col.AGENTS)
        .where(filter=FieldFilter("owner_uid", "==", uid))
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(MAX_DASHBOARD_AGENTS)
    )
    agents = [AgentDoc.model_validate(snap.to_dict()) async for snap in q.stream()]
    return [a for a in agents if not a.deleted]  # in memory (§5.3)


async def _income_figures(uid: str, now: datetime) -> tuple[dict[str, int], list[DocumentSnapshot]]:
    """Sum the creator's income **in memory** over one streamed query (no aggregation, see module doc).

    Returns the cent totals and the newest ``RECENT_INCOME_LIMIT`` full snapshots for ``recent_income``.
    """
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    totals = {"visitor": 0, "a2a": 0, "today": 0, "7d": 0}
    recent: list[DocumentSnapshot] = []
    async for snap in _income_query(uid).limit(MAX_DASHBOARD_INCOME_ROWS).stream():
        if len(recent) < RECENT_INCOME_LIMIT:
            recent.append(snap)
        data = snap.to_dict() or {}
        amount = int(data.get("amount_cents") or 0)
        category = data.get("category")
        if category == money.CREATOR_INCOME:
            totals["visitor"] += amount
        elif category == money.A2A_INCOME:
            totals["a2a"] += amount
        else:  # pragma: no cover - the query only returns income categories
            continue
        created = data.get("created_at")
        if isinstance(created, datetime):
            if created >= today:
                totals["today"] += amount
            if created >= week_ago:
                totals["7d"] += amount
    return totals, recent


async def dashboard(uid: str) -> dict[str, Any]:
    """``Dashboard`` (§6.3). Today = the current UTC day; income figures are the creator's share."""
    balance = await get_or_open_wallet(uid)
    totals, recent = await _income_figures(uid, utcnow())
    stats = await _owner_stats(uid)
    agents = await _owner_agents(uid)
    return {
        "balance_cents": balance,
        "earned_total_cents": totals["visitor"] + totals["a2a"],
        "earned_today_cents": totals["today"],
        "earned_7d_cents": totals["7d"],
        "visitor_income_cents": totals["visitor"],
        "a2a_income_cents": totals["a2a"],
        "paid_rounds": sum(int(s.get("paid_rounds") or 0) for s in stats.values()),
        "a2a_calls": sum(int(s.get("a2a_calls") or 0) for s in stats.values()),
        "agents": [agent_summary_wire(a, stats.get(a.id)) for a in agents],
        "recent_income": await ledger_entries_wire(recent),
    }


# --------------------------------------------------------------------------- agent earnings


async def agent_earnings(
    agent: AgentDoc, *, cursor: str | None, limit: int
) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    """``(AgentStats, LedgerEntry[], next_cursor)``: the owner's ledger rows for this agent, newest first.

    The owner's entries for their own agent are always income, so no category filter (§5.3).
    """
    stats_snap = await get_db().collection(Col.AGENT_STATS).document(agent.id).get()
    snaps, next_cursor = await fetch_page(
        _ledger_query(agent.owner_uid, agent_id=agent.id),
        collection=get_db().collection(Col.LEDGER),
        cursor=cursor,
        limit=limit,
    )
    stats = stats_wire(stats_snap.to_dict() if stats_snap.exists else None)
    return stats, await ledger_entries_wire(snaps), next_cursor
