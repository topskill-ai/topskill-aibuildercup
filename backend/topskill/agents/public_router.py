"""Public agent routes (SPEC §6.4 Agents — public), prefix ``/api/public``. No auth."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query
from fastapi.responses import Response
from google.cloud import firestore

from topskill.agents.avatar import read_avatar
from topskill.agents.schemas import (
    PublicAgentOut,
    PublicAgentPage,
    agent_urls,
    avatar_url,
    resolve_starter_questions,
)
from topskill.agents.service import get_stats
from topskill.db import Col, get_db
from topskill.domain import AgentDoc, AgentVersion
from topskill.pagination import decode_cursor, encode_cursor
from topskill.repo import get_public_agent

router = APIRouter(tags=["public-agents"])

MARKETPLACE_SCAN = 200
DEFAULT_PAGE = 24
MAX_PAGE = 100
AVATAR_CACHE = "public, max-age=31536000, immutable"


async def _has_ready_knowledge(agent_id: str) -> bool:
    query = (
        get_db()
        .collection(Col.AGENTS)
        .document(agent_id)
        .collection(Col.KNOWLEDGE)
        .where(filter=firestore.FieldFilter("status", "==", "ready"))
        .limit(1)
    )
    async for _ in query.stream():
        return True
    return False


async def _active_versions(agents: list[AgentDoc]) -> dict[str, AgentVersion]:
    db = get_db()
    refs = [
        db.collection(Col.AGENTS).document(a.id).collection(Col.VERSIONS).document(str(a.active_version))
        for a in agents
    ]
    out: dict[str, AgentVersion] = {}
    if not refs:
        return out
    async for snap in db.get_all(refs):
        if snap.exists:
            version = AgentVersion.model_validate(snap.to_dict())
            out[snap.reference.parent.parent.id] = version
    return out


async def _cards(agents: list[AgentDoc]) -> list[dict]:
    if not agents:
        return []
    versions, stats, knowledge = await asyncio.gather(
        _active_versions(agents),
        get_stats([a.id for a in agents]),
        asyncio.gather(*(_has_ready_knowledge(a.id) for a in agents)),
    )
    return [
        card_to_wire(a, versions.get(a.id), stats.get(a.id) or {}, has_knowledge)
        for a, has_knowledge in zip(agents, knowledge, strict=True)
    ]


def card_to_wire(agent: AgentDoc, version: AgentVersion | None, stats: dict, has_knowledge: bool) -> dict:
    """TS ``PublicAgentCard``. Feature flags follow the active version (the config may be dirty)."""
    web = version.web_search_enabled if version else agent.config.web_search_enabled
    records = (version.record_schema if version else agent.config.record_schema) is not None
    return {
        "slug": agent.slug,
        "title": agent.title,
        "tagline": agent.tagline,
        "avatar_url": avatar_url(agent.avatar_id),
        "accent_color": agent.accent_color,
        "creator": {"uid": agent.owner_uid, "name": agent.creator_name, "headline": agent.creator_headline},
        "pricing": {
            "mode": agent.pricing.mode,
            "price_cents": agent.pricing.price_cents,
            "free_rounds": agent.pricing.free_rounds,
        },
        "features": {
            "knowledge": has_knowledge,
            "web_search": web,
            "records": records,
            "a2a": agent.pricing.a2a_enabled,
        },
        "stats": {
            "conversations": int(stats.get("conversations") or 0),
            "a2a_calls": int(stats.get("a2a_calls") or 0),
        },
    }


def _matches(agent: AgentDoc, q: str) -> bool:
    return q in agent.title.lower() or q in agent.tagline.lower()


@router.get("/agents", response_model=PublicAgentPage)
async def list_public_agents(
    q: str = Query("", max_length=100),
    cursor: str | None = Query(None, max_length=500),
    limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
) -> dict:
    """Listed, active, not deleted (index §5.3); ``q`` filtered **in memory** over the 200 newest."""
    query = (
        get_db()
        .collection(Col.AGENTS)
        .where(filter=firestore.FieldFilter("listed", "==", True))
        .where(filter=firestore.FieldFilter("status", "==", "active"))
        .where(filter=firestore.FieldFilter("deleted", "==", False))
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(MARKETPLACE_SCAN)
    )
    agents = [AgentDoc.model_validate(snap.to_dict()) async for snap in query.stream()]
    agents.sort(key=lambda a: (a.created_at, a.id), reverse=True)
    needle = q.strip().lower()
    if needle:
        agents = [a for a in agents if _matches(a, needle)]
    if cursor:
        after_id, after_ts = decode_cursor(cursor)
        agents = [a for a in agents if (a.created_at, a.id) < (after_ts, after_id)]
    page = agents[:limit]
    next_cursor = encode_cursor(page[-1].id, page[-1].created_at) if len(agents) > limit and page else None
    return {"items": await _cards(page), "next_cursor": next_cursor}


@router.get("/agents/{slug}", response_model=PublicAgentOut)
async def get_public_agent_page(slug: str) -> dict:
    agent = await get_public_agent(slug)  # 404 agent_not_found / 410 agent_offline
    card = (await _cards([agent]))[0]
    urls = agent_urls(agent.slug)
    return {
        **card,
        "description": agent.description,
        "greeting": agent.greeting,
        "input_placeholder": agent.input_placeholder,
        "starter_questions": [q.model_dump() for q in resolve_starter_questions(agent)],
        "capabilities": [c.model_dump() for c in agent.capabilities],
        "a2a": {
            "enabled": agent.pricing.a2a_enabled,
            "price_cents": agent.pricing.a2a_price_cents,
            "card_url": urls["a2a_card"],
            "endpoint": urls["a2a_endpoint"],
        },
        "status": "active",
    }


@router.get("/avatars/{avatar_id}", response_class=Response)
async def get_avatar(avatar_id: str) -> Response:
    data = await read_avatar(avatar_id)  # 404 not_found
    return Response(content=data, media_type="image/webp", headers={"Cache-Control": AVATAR_CACHE})
