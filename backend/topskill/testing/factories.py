"""Test data written straight to the Firestore emulator (SPEC §12.1).

Every helper generates unique random ids, so tests never need to wipe the shared emulator. Documents
follow the shapes of SPEC §5.1/§5.2 exactly, so domain code can read them back.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from topskill.auth import CurrentUser, ensure_user, to_current_user
from topskill.db import Col, get_db, new_id, utcnow
from topskill.domain import (
    AgentConfig,
    AgentDoc,
    AgentVersion,
    ConversationDoc,
    MessageDoc,
    Pricing,
    Role,
    SkillFile,
    SkillRef,
)
from topskill.storage import get_blob_store


def unique(prefix: str = "t") -> str:
    """A short unique token for test ids/slugs (lowercase hex)."""
    return f"{prefix}{secrets.token_hex(5)}"


async def make_user(
    role: Role = "visitor",
    *,
    uid: str | None = None,
    email: str | None = None,
    display_name: str | None = None,
    headline: str = "",
) -> CurrentUser:
    """Create ``users/{uid}`` (role applied) and return the matching ``CurrentUser``."""
    uid = uid or unique("u_")
    email = email or f"{uid}@example.test"
    doc = await ensure_user(uid, email=email, name=display_name, role=role, update_role=True)
    if role == "creator" and headline:
        profile = {"headline": headline, "bio": ""}
        await get_db().collection(Col.USERS).document(uid).update({"creator_profile": profile})
    return to_current_user(doc)


async def open_wallet(uid: str, cents: int) -> int:
    """Create (or overwrite) ``wallets/{uid}`` with ``cents`` plus the ``signup`` ledger row."""
    now = utcnow()
    db = get_db()
    batch = db.batch()
    batch.set(
        db.collection(Col.WALLETS).document(uid),
        {"uid": uid, "balance_cents": cents, "opened_at": now, "updated_at": now},
    )
    batch.set(
        db.collection(Col.LEDGER).document(f"{uid}__signup"),
        {
            "uid": uid,
            "request_id": "signup",
            "kind": "CREDIT",
            "category": "signup_grant",
            "amount_cents": cents,
            "balance_after_cents": cents,
            "unit_price_cents": None,
            "billing_mode": None,
            "counterparty_uid": None,
            "agent_id": None,
            "agent_slug": None,
            "agent_title": None,
            "conversation_id": None,
            "mandate_id": None,
            "a2a_call_id": None,
            "round_index": None,
            "model": None,
            "memo": "Welcome credits",
            "created_at": now,
        },
    )
    await batch.commit()
    return cents


DEFAULT_SKILL_BODY = """---
name: test-skill
description: A small playbook used in tests.
---
# Test playbook
Answer questions briefly and ask one clarifying question when needed.
"""


async def make_skill(
    owner: CurrentUser | str,
    body: str = DEFAULT_SKILL_BODY,
    *,
    name: str = "Test Skill",
    slug: str = "test-skill",
    description: str = "A small playbook used in tests.",
    extra_files: dict[str, bytes] | None = None,
) -> SkillRef:
    """Write ``skills/{id}`` plus its files to the blob store; returns the ``SkillRef`` snapshot."""
    owner_uid = owner if isinstance(owner, str) else owner.uid
    skill_id = new_id("skl")
    prefix = f"skills/{skill_id}/"
    store = get_blob_store()
    body_bytes = body.encode()
    files = [SkillFile(path="SKILL.md", size=len(body_bytes), mime="text/markdown", is_text=True)]
    await store.put(prefix + "SKILL.md", body_bytes, "text/markdown")
    for path, data in (extra_files or {}).items():
        is_text = path.endswith((".md", ".txt", ".csv", ".json", ".yaml", ".yml"))
        mime = (
            "text/markdown"
            if path.endswith(".md")
            else ("text/plain" if is_text else "application/octet-stream")
        )
        await store.put(prefix + path, data, mime)
        files.append(SkillFile(path=path, size=len(data), mime=mime, is_text=is_text))
    ref = SkillRef(
        skill_id=skill_id,
        slug=slug,
        name=name,
        description=description,
        gcs_prefix=prefix,
        body_chars=len(body),
        files=files,
    )
    await (
        get_db()
        .collection(Col.SKILLS)
        .document(skill_id)
        .set(
            {
                "id": skill_id,
                "owner_uid": owner_uid,
                "slug": slug,
                "name": name,
                "description": description,
                "body_chars": len(body),
                "gcs_prefix": prefix,
                "files": [f.model_dump() for f in files],
                "created_at": utcnow(),
            }
        )
    )
    return ref


def _version_from(
    agent: AgentDoc, n: int, skills: list[SkillRef], created_by: str, note: str
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
    )


async def _skill_refs(skill_ids: list[str]) -> list[SkillRef]:
    refs: list[SkillRef] = []
    for skill_id in skill_ids:
        snap = await get_db().collection(Col.SKILLS).document(skill_id).get()
        if not snap.exists:
            continue
        data = snap.to_dict() or {}
        refs.append(
            SkillRef(
                skill_id=skill_id,
                slug=data.get("slug", "skill"),
                name=data.get("name", "Skill"),
                description=data.get("description", ""),
                gcs_prefix=data.get("gcs_prefix", f"skills/{skill_id}/"),
                body_chars=int(data.get("body_chars", 0)),
                files=[SkillFile.model_validate(f) for f in data.get("files", [])],
            )
        )
    return refs


async def make_agent(
    owner: CurrentUser,
    *,
    open_owner_wallet: bool = True,
    owner_balance_cents: int = 10_000,
    **overrides: Any,
) -> AgentDoc:
    """Create a published agent: ``agents/{id}``, ``versions/1``, ``slugs/{slug}``, ``agent_stats/{id}``.

    ``overrides`` set ``AgentDoc`` fields; ``pricing`` / ``config`` may be models or (partial) dicts.
    A skill is created for the owner unless ``config`` carries ``skill_ids``.
    """
    now = utcnow()
    agent_id = overrides.pop("id", None) or new_id("agt")
    slug = overrides.pop("slug", None) or f"agent-{secrets.token_hex(4)}"

    pricing_in = overrides.pop("pricing", None)
    if isinstance(pricing_in, Pricing):
        pricing = pricing_in
    else:
        pricing = Pricing.model_validate(
            {
                "mode": "per_round",
                "price_cents": 100,
                "free_rounds": 2,
                "a2a_enabled": True,
                "a2a_price_cents": 200,
                **(pricing_in or {}),
            }
        )

    config_in = overrides.pop("config", None)
    config_data: dict[str, Any] = (
        config_in.model_dump() if isinstance(config_in, AgentConfig) else dict(config_in or {})
    )
    if not config_data.get("skill_ids"):
        skill = await make_skill(owner)
        config_data["skill_ids"] = [skill.skill_id]
    config_data.setdefault("soul", "You are a helpful test agent.")
    config = AgentConfig.model_validate(config_data)

    agent = AgentDoc.model_validate(
        {
            "id": agent_id,
            "owner_uid": owner.uid,
            "slug": slug,
            "title": overrides.pop("title", "Test Agent"),
            "tagline": overrides.pop("tagline", "An agent used in tests"),
            "creator_name": overrides.pop("creator_name", owner.display_name),
            "pricing": pricing,
            "config": config,
            "created_at": now,
            "updated_at": now,
            **overrides,
        }
    )
    skills = await _skill_refs(config.skill_ids)
    version = _version_from(agent, agent.active_version, skills, owner.uid, "Initial version")

    if open_owner_wallet and not (await get_db().collection(Col.WALLETS).document(owner.uid).get()).exists:
        await open_wallet(owner.uid, owner_balance_cents)  # a second agent never resets the owner's balance

    db = get_db()
    batch = db.batch()
    agent_ref = db.collection(Col.AGENTS).document(agent_id)
    batch.set(agent_ref, agent.model_dump(mode="python"))
    batch.set(agent_ref.collection(Col.VERSIONS).document(str(version.n)), version.model_dump(mode="python"))
    batch.set(db.collection(Col.SLUGS).document(slug), {"agent_id": agent_id, "created_at": now})
    batch.set(
        db.collection(Col.AGENT_STATS).document(agent_id),
        {
            "agent_id": agent_id,
            "owner_uid": owner.uid,
            "rounds": 0,
            "paid_rounds": 0,
            "conversations": 0,
            "visitor_gross_cents": 0,
            "creator_earned_cents": 0,
            "a2a_calls": 0,
            "a2a_gross_cents": 0,
            "records": 0,
            "last_activity_at": None,
        },
    )
    await batch.commit()
    return agent


async def make_version(
    agent: AgentDoc, *, note: str = "", activate: bool = True, **overrides: Any
) -> AgentVersion:
    """Snapshot the agent's current config as version ``version_count + 1`` (activated by default)."""
    n = agent.version_count + 1
    skills = await _skill_refs(agent.config.skill_ids)
    version = _version_from(agent, n, skills, agent.owner_uid, note).model_copy(update=overrides)
    db = get_db()
    agent_ref = db.collection(Col.AGENTS).document(agent.id)
    updates: dict[str, Any] = {"version_count": n, "updated_at": utcnow()}
    if activate:
        updates.update({"active_version": n, "config_dirty": False, "status": "active"})
    batch = db.batch()
    batch.set(agent_ref.collection(Col.VERSIONS).document(str(n)), version.model_dump(mode="python"))
    batch.update(agent_ref, updates)
    await batch.commit()
    return version


async def make_conversation(
    agent: AgentDoc,
    visitor: CurrentUser,
    *,
    turns: list[tuple[str, str]] | None = None,
    title: str | None = None,
) -> ConversationDoc:
    """A persisted conversation with ``turns`` = ``[(user_text, model_text), …]`` (each turn one ok round)."""
    turns = turns or [("Hello", "Hi! How can I help?")]
    db = get_db()
    cid = new_id("cnv")
    conv_ref = db.collection(Col.CONVERSATIONS).document(cid)
    batch = db.batch()
    base = utcnow()
    for i, (user_text, model_text) in enumerate(turns):
        request_id = f"turn_{secrets.token_hex(16)}"
        turn_id = f"t{secrets.token_hex(6)}"
        for role, text, offset in (("user", user_text, 0), ("model", model_text, 1)):
            msg = MessageDoc(
                id=("u_" if role == "user" else "m_") + request_id,
                role=role,  # type: ignore[arg-type]
                text=text,
                status="ok",
                turn_id=turn_id,
                request_id=request_id,
                created_at=base + timedelta(seconds=i * 2 + offset),
            )
            batch.set(conv_ref.collection(Col.MESSAGES).document(msg.id), msg.model_dump(mode="python"))
    conv = ConversationDoc(
        id=cid,
        agent_id=agent.id,
        agent_slug=agent.slug,
        owner_uid=agent.owner_uid,
        visitor_uid=visitor.uid,
        title=(title or turns[0][0])[:80],
        rounds_ok=len(turns),
        message_count=2 * len(turns),
        created_at=base,
        updated_at=base,
    )
    batch.set(conv_ref, conv.model_dump(mode="python"))
    await batch.commit()
    return conv
