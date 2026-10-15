"""Agents: publish (T5), versions (T6), live/config updates, pause/resume/delete, and wire serialisation
(SPEC §5.4, §6.4, §8.7, §8.8)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from google.api_core import exceptions as gexc
from google.cloud import firestore

from topskill.agents import deps
from topskill.agents.avatar import avatar_exists, is_avatar_id
from topskill.agents.schemas import (
    ConfigPatch,
    CreateAgentRequest,
    UpdateAgentRequest,
    agent_urls,
    avatar_url,
)
from topskill.agents.slugs import auto_slug, format_problem, is_taken, normalize_slug, slug_error
from topskill.auth import CurrentUser
from topskill.config import get_settings
from topskill.db import Col, get_db, new_id, utcnow
from topskill.domain import (
    AgentConfig,
    AgentDoc,
    AgentVersion,
    Pricing,
    RecordSchema,
    RecordSchemaIn,
    SkillRef,
    iso_z,
)
from topskill.errors import ApiError, Codes
from topskill.repo import get_owned_agent, get_user, get_version
from topskill.skills.service import get_skill_docs, load_skill_refs, skill_to_wire

log = logging.getLogger(__name__)

AUTO_SLUG_ATTEMPTS = 5
MAX_AGENTS_LISTED = 100
_LIVE_FIELDS = (
    "title",
    "tagline",
    "description",
    "greeting",
    "input_placeholder",
    "accent_color",
    "listed",
    "creator_name",
    "creator_headline",
)
_STATS_ZERO: dict[str, Any] = {
    "rounds": 0,
    "paid_rounds": 0,
    "conversations": 0,
    "visitor_gross_cents": 0,
    "creator_earned_cents": 0,
    "a2a_calls": 0,
    "a2a_gross_cents": 0,
    "records": 0,
    "last_activity_at": None,
}


# --------------------------------------------------------------------------- validation helpers


def normalize_pricing(pricing: Pricing) -> Pricing:
    """``free`` forces ``price_cents = 0``; ``per_round`` needs ``price_cents >= 1`` (else 400
    ``pricing_invalid``)."""
    if pricing.mode == "free":
        return pricing.model_copy(update={"price_cents": 0})
    if pricing.price_cents < 1:
        raise ApiError(
            400,
            Codes.PRICING_INVALID,
            "A paid agent needs a price of at least 0.01 credits per round.",
            {"price_cents": pricing.price_cents},
        )
    return pricing


def check_model(model: str) -> str:
    allowed = get_settings().chat_model_allowlist
    if model not in allowed:
        raise ApiError(
            400, Codes.MODEL_NOT_ALLOWED, "This model is not available.", {"model": model, "allowed": allowed}
        )
    return model


def validate_record_schema(raw: RecordSchemaIn | dict | None) -> RecordSchema | None:
    """Strict ``records.validate_schema``; ``{fields: []}`` / ``None`` → ``None``."""
    if raw is None:
        return None
    try:
        return deps.records().validate_schema(raw, lenient=False)
    except ApiError:
        raise
    except ValueError as exc:  # pydantic ValidationError from the pass-through stub
        raise ApiError(
            400, Codes.RECORD_SCHEMA_INVALID, "The record fields are invalid.", {"errors": [str(exc)[:500]]}
        ) from exc


async def check_avatar(avatar_id: str | None) -> str | None:
    if avatar_id is None:
        return None
    if not is_avatar_id(avatar_id) or not await avatar_exists(avatar_id):
        raise ApiError(
            400,
            Codes.VALIDATION_ERROR,
            "The avatar does not exist. Upload or generate it again.",
            {"errors": [{"loc": ["body", "avatar_id"], "msg": "unknown avatar"}]},
        )
    return avatar_id


def build_version(
    agent: AgentDoc, n: int, skills: list[SkillRef], created_by: str, note: str, knowledge_ids: list[str]
) -> AgentVersion:
    return AgentVersion(
        n=n,
        created_at=utcnow(),
        created_by=created_by,
        note=note,
        title=agent.title,
        soul=agent.config.soul,
        model=agent.config.model,
        temperature=agent.config.temperature,
        web_search_enabled=agent.config.web_search_enabled,
        skills=skills,
        record_schema=agent.config.record_schema,
        knowledge_file_ids=knowledge_ids,
    )


def config_matches_version(config: AgentConfig, version: AgentVersion | None) -> bool:
    """True when the runtime config equals what ``version`` was built from."""
    if version is None:
        return False

    def schema_dump(s: RecordSchema | None) -> Any:
        return s.model_dump() if s is not None else None

    return (
        config.soul == version.soul
        and config.model == version.model
        and config.temperature == version.temperature
        and config.web_search_enabled == version.web_search_enabled
        and list(config.skill_ids) == [s.skill_id for s in version.skills]
        and schema_dump(config.record_schema) == schema_dump(version.record_schema)
    )


async def ready_knowledge_ids(agent_id: str) -> list[str]:
    """Ids of ``ready`` knowledge files (single-field equality query) — informational version snapshot."""
    query = (
        get_db()
        .collection(Col.AGENTS)
        .document(agent_id)
        .collection(Col.KNOWLEDGE)
        .where(filter=firestore.FieldFilter("status", "==", "ready"))
    )
    return [snap.id async for snap in query.stream()]


# --------------------------------------------------------------------------- publish (T5)


async def create_agent(owner: CurrentUser, req: CreateAgentRequest) -> AgentDoc:
    """Validate everything, open the owner's wallet, then create slug + agent + version 1 + stats (T5)."""
    pricing = normalize_pricing(req.pricing)
    settings = get_settings()
    model = check_model(req.config.model or settings.model_chat)
    record_schema = validate_record_schema(req.config.record_schema)
    skill_ids = list(dict.fromkeys(req.skill_ids))
    refs = await load_skill_refs(owner.uid, skill_ids)
    avatar_id = await check_avatar(req.avatar_id)

    custom_slug: str | None = None
    if req.slug:
        custom_slug = normalize_slug(req.slug)
        reason = format_problem(custom_slug)
        if reason:
            raise slug_error(reason)
        if await is_taken(custom_slug):
            raise slug_error(Codes.SLUG_TAKEN)

    user_doc = await get_user(owner.uid)
    profile = (user_doc.creator_profile if user_doc else None) or {}
    default_name = user_doc.display_name if user_doc else owner.display_name
    creator_name = (req.creator_name or "").strip() or default_name
    creator_headline = (req.creator_headline or "").strip() or str(profile.get("headline") or "")

    await deps.billing().get_or_open_wallet(owner.uid)  # owners always have a wallet (SPEC §5.1)

    now = utcnow()
    agent_id = new_id("agt")
    config = AgentConfig(
        soul=req.config.soul.strip(),
        model=model,
        temperature=req.config.temperature,
        web_search_enabled=req.config.web_search_enabled,
        skill_ids=[r.skill_id for r in refs],
        record_schema=record_schema,
    )
    db = get_db()
    for attempt in range(1 if custom_slug else AUTO_SLUG_ATTEMPTS):
        slug = custom_slug or auto_slug(req.title)
        agent = AgentDoc(
            id=agent_id,
            owner_uid=owner.uid,
            slug=slug,
            status="active",
            listed=req.listed,
            title=req.title,
            tagline=req.tagline,
            description=req.description,
            avatar_id=avatar_id,
            greeting=req.greeting,
            input_placeholder=req.input_placeholder,
            starter_questions=req.starter_questions,
            capabilities=req.capabilities,
            accent_color=req.accent_color,
            creator_name=creator_name,
            creator_headline=creator_headline,
            pricing=pricing,
            config=config,
            config_dirty=False,
            active_version=1,
            version_count=1,
            created_at=now,
            updated_at=now,
        )
        version = build_version(agent, 1, refs, owner.uid, "Initial version", [])
        try:
            await _publish_tx(db, agent, version)
        except ApiError as exc:
            if exc.code == Codes.SLUG_TAKEN and not custom_slug and attempt + 1 < AUTO_SLUG_ATTEMPTS:
                continue
            raise
        log.info("agent_published agent_id=%s slug=%s owner=%s", agent_id, slug, owner.uid)
        return agent
    raise slug_error(Codes.SLUG_TAKEN)  # pragma: no cover - loop always returns or raises


async def _publish_tx(db: firestore.AsyncClient, agent: AgentDoc, version: AgentVersion) -> None:
    slug_ref = db.collection(Col.SLUGS).document(agent.slug)
    agent_ref = db.collection(Col.AGENTS).document(agent.id)
    version_ref = agent_ref.collection(Col.VERSIONS).document("1")
    stats_ref = db.collection(Col.AGENT_STATS).document(agent.id)

    @firestore.async_transactional
    async def _tx(tx: Any) -> None:
        snap = await slug_ref.get(transaction=tx)
        if snap.exists:
            raise slug_error(Codes.SLUG_TAKEN)
        tx.create(slug_ref, {"agent_id": agent.id, "created_at": agent.created_at})
        tx.create(agent_ref, agent.model_dump(mode="python"))
        tx.create(version_ref, version.model_dump(mode="python"))
        tx.create(stats_ref, {"agent_id": agent.id, "owner_uid": agent.owner_uid, **_STATS_ZERO})

    try:
        await _tx(db.transaction())
    except (gexc.AlreadyExists, gexc.Conflict, gexc.FailedPrecondition) as exc:
        raise slug_error(Codes.SLUG_TAKEN) from exc


# --------------------------------------------------------------------------- versions (T6)


async def create_version(agent: AgentDoc, user: CurrentUser, note: str) -> AgentDoc:
    """Rebuild a version from the current config (skills re-validated as owned), activate it,
    ``status=active``, ``config_dirty=false``."""
    knowledge_ids = await ready_knowledge_ids(agent.id)
    db = get_db()
    agent_ref = db.collection(Col.AGENTS).document(agent.id)
    skill_ids = list(agent.config.skill_ids)
    for _ in range(3):
        refs = await load_skill_refs(user.uid, skill_ids)
        try:
            updated = await _version_tx(db, agent_ref, user, refs, note, knowledge_ids)
        except _SkillsChanged as changed:
            skill_ids = changed.skill_ids  # a concurrent PATCH changed the skills: validate those instead
            continue
        log.info("agent_version_created agent_id=%s n=%d", agent.id, updated.active_version)
        return updated
    raise ApiError(503, Codes.SERVICE_UNAVAILABLE, "The agent is being edited. Please try again.")


class _SkillsChanged(Exception):
    def __init__(self, skill_ids: list[str]) -> None:
        super().__init__("skills changed")
        self.skill_ids = skill_ids


async def _version_tx(
    db: firestore.AsyncClient,
    agent_ref: Any,
    user: CurrentUser,
    refs: list[SkillRef],
    note: str,
    knowledge_ids: list[str],
) -> AgentDoc:
    @firestore.async_transactional
    async def _tx(tx: Any) -> AgentDoc:
        snap = await agent_ref.get(transaction=tx)
        if not snap.exists:
            raise ApiError(404, Codes.AGENT_NOT_FOUND, "This agent does not exist.")
        current = AgentDoc.model_validate(snap.to_dict())
        if current.deleted or current.owner_uid != user.uid:
            raise ApiError(404, Codes.AGENT_NOT_FOUND, "This agent does not exist.")
        if list(current.config.skill_ids) != [r.skill_id for r in refs]:
            raise _SkillsChanged(list(current.config.skill_ids))
        n = current.version_count + 1
        # Skills were re-validated as owned for exactly this config (SPEC §8.8).
        version = build_version(current, n, refs, user.uid, note.strip(), knowledge_ids)
        now = utcnow()
        updates = {
            "active_version": n,
            "version_count": n,
            "config_dirty": False,
            "status": "active",
            "updated_at": now,
        }
        tx.create(agent_ref.collection(Col.VERSIONS).document(str(n)), version.model_dump(mode="python"))
        tx.update(agent_ref, updates)
        return current.model_copy(update=updates)

    return await _tx(db.transaction())


async def list_versions(agent: AgentDoc) -> list[dict]:
    query = (
        get_db()
        .collection(Col.AGENTS)
        .document(agent.id)
        .collection(Col.VERSIONS)
        .order_by("n", direction=firestore.Query.DESCENDING)
        .limit(100)
    )
    items: list[dict] = []
    async for snap in query.stream():
        v = AgentVersion.model_validate(snap.to_dict())
        items.append(
            {
                "n": v.n,
                "created_at": iso_z(v.created_at),
                "note": v.note,
                "model": v.model,
                "web_search_enabled": v.web_search_enabled,
                "skills": [{"name": s.name, "skill_id": s.skill_id} for s in v.skills],
                "records_enabled": v.record_schema is not None,
                "active": v.n == agent.active_version,
            }
        )
    return items


async def activate_version(agent: AgentDoc, n: int) -> AgentDoc:
    """Rollback: activate an existing version. Config is not rewritten; ``config_dirty`` is set when the
    config differs from that version."""
    version = await get_version(agent.id, n)
    if version is None:
        raise ApiError(404, Codes.NOT_FOUND, "Version not found.")
    updates = {
        "active_version": n,
        "config_dirty": not config_matches_version(agent.config, version),
        "updated_at": utcnow(),
    }
    await get_db().collection(Col.AGENTS).document(agent.id).update(updates)
    return agent.model_copy(update=updates)


# --------------------------------------------------------------------------- updates


async def update_agent(agent: AgentDoc, user: CurrentUser, req: UpdateAgentRequest) -> AgentDoc:
    """``PATCH``: live fields apply now; config fields mark ``config_dirty`` (vs the active version)."""
    sent = req.model_fields_set
    updates: dict[str, Any] = {}
    for name in _LIVE_FIELDS:
        if name in sent:
            value = getattr(req, name)
            if value is None:
                if name in ("creator_name", "creator_headline"):
                    value = ""
                else:
                    raise _null_not_allowed(name)
            updates[name] = value.strip() if isinstance(value, str) else value
    if "title" in updates and not updates["title"]:
        raise _null_not_allowed("title")
    if "avatar_id" in sent:
        updates["avatar_id"] = await check_avatar(req.avatar_id)
    if "starter_questions" in sent:
        updates["starter_questions"] = (
            [q.model_dump() for q in req.starter_questions] if req.starter_questions is not None else None
        )
    if "capabilities" in sent:
        if req.capabilities is None:
            raise _null_not_allowed("capabilities")
        updates["capabilities"] = [c.model_dump() for c in req.capabilities]
    if "pricing" in sent:
        if req.pricing is None:
            raise _null_not_allowed("pricing")
        updates["pricing"] = normalize_pricing(req.pricing).model_dump()

    new_config = agent.config
    if "config" in sent and req.config is not None:
        new_config = await _patched_config(agent, user, req.config)
        updates["config"] = new_config.model_dump(mode="python")
        active = await get_version(agent.id, agent.active_version)
        updates["config_dirty"] = not config_matches_version(new_config, active)

    if not updates:
        return agent
    updates["updated_at"] = utcnow()
    await get_db().collection(Col.AGENTS).document(agent.id).update(updates)
    merged = agent.model_dump(mode="python")
    merged.update(updates)
    return AgentDoc.model_validate(merged)


def _null_not_allowed(name: str) -> ApiError:
    return ApiError(
        400,
        Codes.VALIDATION_ERROR,
        f"'{name}' cannot be empty.",
        {"errors": [{"loc": ["body", name], "msg": "must not be null or blank"}]},
    )


async def _patched_config(agent: AgentDoc, user: CurrentUser, patch: ConfigPatch) -> AgentConfig:
    sent = patch.model_fields_set
    data = agent.config.model_dump(mode="python")
    if "soul" in sent:
        if patch.soul is None:
            raise _null_not_allowed("config.soul")
        data["soul"] = patch.soul.strip()
    if "model" in sent:
        if patch.model is None:
            raise _null_not_allowed("config.model")
        data["model"] = check_model(patch.model)
    if "temperature" in sent:
        data["temperature"] = patch.temperature
    if "web_search_enabled" in sent:
        if patch.web_search_enabled is None:
            raise _null_not_allowed("config.web_search_enabled")
        data["web_search_enabled"] = patch.web_search_enabled
    if "skill_ids" in sent:
        if not patch.skill_ids:
            raise _null_not_allowed("config.skill_ids")
        refs = await load_skill_refs(user.uid, list(dict.fromkeys(patch.skill_ids)))
        data["skill_ids"] = [r.skill_id for r in refs]
    if "record_schema" in sent:
        new_schema = validate_record_schema(patch.record_schema)
        await deps.records().assert_schema_change_allowed(agent.id, agent.config.record_schema, new_schema)
        data["record_schema"] = new_schema.model_dump(mode="python") if new_schema is not None else None
    return AgentConfig.model_validate(data)


async def set_status(agent: AgentDoc, status: str) -> AgentDoc:
    updates = {"status": status, "updated_at": utcnow()}
    await get_db().collection(Col.AGENTS).document(agent.id).update(updates)
    return agent.model_copy(update=updates)


async def soft_delete(agent: AgentDoc) -> None:
    """``deleted=true, status=paused, listed=false``; the slug stays reserved."""
    await (
        get_db()
        .collection(Col.AGENTS)
        .document(agent.id)
        .update({"deleted": True, "status": "paused", "listed": False, "updated_at": utcnow()})
    )
    log.info("agent_deleted agent_id=%s", agent.id)


async def owned_agent(agent_id: str, user: CurrentUser) -> AgentDoc:
    return await get_owned_agent(agent_id, user)


# --------------------------------------------------------------------------- reads / serialisation


def stats_to_wire(data: dict | None) -> dict:
    out = dict(_STATS_ZERO)
    for key in out:
        if data and key in data and data[key] is not None:
            out[key] = data[key]
    last = out.get("last_activity_at")
    out["last_activity_at"] = iso_z(last) if isinstance(last, datetime) else None
    for key, value in list(out.items()):
        if key != "last_activity_at":
            out[key] = int(value or 0)
    return out


async def get_stats(agent_ids: list[str]) -> dict[str, dict]:
    db = get_db()
    refs = [db.collection(Col.AGENT_STATS).document(a) for a in dict.fromkeys(agent_ids)]
    out: dict[str, dict] = {}
    if not refs:
        return out
    async for snap in db.get_all(refs):
        out[snap.id] = snap.to_dict() if snap.exists else {}
    return out


async def knowledge_counts(agent_id: str) -> dict:
    """``{files, ready, processing, failed}`` from the knowledge subcollection (status projection)."""
    query = get_db().collection(Col.AGENTS).document(agent_id).collection(Col.KNOWLEDGE).select(["status"])
    counts = {"files": 0, "ready": 0, "processing": 0, "failed": 0}
    async for snap in query.stream():
        status = (snap.to_dict() or {}).get("status")
        counts["files"] += 1
        if status == "ready":
            counts["ready"] += 1
        elif status == "processing":
            counts["processing"] += 1
        else:
            counts["failed"] += 1
    return counts


async def agent_to_wire(agent: AgentDoc) -> dict:
    """TS ``Agent``."""
    skill_docs, stats, knowledge = await asyncio.gather(
        get_skill_docs(list(agent.config.skill_ids)),
        get_stats([agent.id]),
        knowledge_counts(agent.id),
    )
    skills = [skill_to_wire(skill_docs[s]) for s in agent.config.skill_ids if s in skill_docs]
    return {
        "id": agent.id,
        "slug": agent.slug,
        "status": agent.status,
        "listed": agent.listed,
        "title": agent.title,
        "tagline": agent.tagline,
        "description": agent.description,
        "avatar_id": agent.avatar_id,
        "avatar_url": avatar_url(agent.avatar_id),
        "greeting": agent.greeting,
        "input_placeholder": agent.input_placeholder,
        "starter_questions": (
            [q.model_dump() for q in agent.starter_questions] if agent.starter_questions is not None else None
        ),
        "capabilities": [c.model_dump() for c in agent.capabilities],
        "accent_color": agent.accent_color,
        "creator_name": agent.creator_name,
        "creator_headline": agent.creator_headline,
        "pricing": agent.pricing.model_dump(),
        "config": agent.config.model_dump(mode="json"),
        "config_dirty": agent.config_dirty,
        "active_version": agent.active_version,
        "version_count": agent.version_count,
        "skills": skills,
        "knowledge": knowledge,
        "stats": stats_to_wire(stats.get(agent.id)),
        "urls": agent_urls(agent.slug),
        "created_at": iso_z(agent.created_at),
        "updated_at": iso_z(agent.updated_at),
    }


def summary_to_wire(agent: AgentDoc, stats: dict | None) -> dict:
    """TS ``AgentSummary``."""
    return {
        "id": agent.id,
        "slug": agent.slug,
        "title": agent.title,
        "tagline": agent.tagline,
        "avatar_url": avatar_url(agent.avatar_id),
        "accent_color": agent.accent_color,
        "status": agent.status,
        "listed": agent.listed,
        "pricing": agent.pricing.model_dump(),
        "active_version": agent.active_version,
        "config_dirty": agent.config_dirty,
        "stats": stats_to_wire(stats),
        "updated_at": iso_z(agent.updated_at),
    }


async def list_owned(owner_uid: str) -> list[AgentDoc]:
    """``owner_uid ==`` ordered by ``created_at`` desc (index §5.3); ``deleted`` filtered in memory."""
    query = (
        get_db()
        .collection(Col.AGENTS)
        .where(filter=firestore.FieldFilter("owner_uid", "==", owner_uid))
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(MAX_AGENTS_LISTED)
    )
    agents = [AgentDoc.model_validate(snap.to_dict()) async for snap in query.stream()]
    return [a for a in agents if not a.deleted]


async def list_summaries(owner_uid: str) -> list[dict]:
    agents = await list_owned(owner_uid)
    stats = await get_stats([a.id for a in agents])
    return [summary_to_wire(a, stats.get(a.id)) for a in agents]
