"""Shared read helpers (SPEC §14.2). Writes stay in the owning package."""

from __future__ import annotations

from google.cloud import firestore

from topskill.auth import CurrentUser
from topskill.db import Col, get_db
from topskill.domain import AgentDoc, AgentVersion, ConversationDoc, MessageDoc, UserDoc
from topskill.errors import ApiError, Codes


def _agent_not_found() -> ApiError:
    return ApiError(404, Codes.AGENT_NOT_FOUND, "This agent does not exist.")


async def get_user(uid: str) -> UserDoc | None:
    snap = await get_db().collection(Col.USERS).document(uid).get()
    return UserDoc.model_validate(snap.to_dict()) if snap.exists else None


async def get_agent(agent_id: str) -> AgentDoc | None:
    if not agent_id or "/" in agent_id:
        return None
    snap = await get_db().collection(Col.AGENTS).document(agent_id).get()
    return AgentDoc.model_validate(snap.to_dict()) if snap.exists else None


async def get_agent_by_slug(slug: str) -> AgentDoc | None:
    if not slug or "/" in slug:
        return None
    slug_snap = await get_db().collection(Col.SLUGS).document(slug).get()
    if not slug_snap.exists:
        return None
    agent_id = (slug_snap.to_dict() or {}).get("agent_id")
    return await get_agent(agent_id) if agent_id else None


async def get_version(agent_id: str, n: int) -> AgentVersion | None:
    snap = await (
        get_db().collection(Col.AGENTS).document(agent_id).collection(Col.VERSIONS).document(str(n)).get()
    )
    return AgentVersion.model_validate(snap.to_dict()) if snap.exists else None


async def get_owned_agent(agent_id: str, user: CurrentUser) -> AgentDoc:
    """The caller's own (not deleted) agent, else 404 ``agent_not_found`` (never leak existence)."""
    agent = await get_agent(agent_id)
    if agent is None or agent.deleted or agent.owner_uid != user.uid:
        raise _agent_not_found()
    return agent


async def get_public_agent(slug: str) -> AgentDoc:
    """Agent by slug for public routes: 404 ``agent_not_found``; 410 ``agent_offline`` when paused/deleted."""
    agent = await get_agent_by_slug(slug)
    if agent is None:
        raise _agent_not_found()
    if agent.deleted or agent.status != "active":
        raise ApiError(410, Codes.AGENT_OFFLINE, "This agent is temporarily unavailable.", {"slug": slug})
    return agent


async def get_active_version(agent: AgentDoc) -> AgentVersion:
    version = await get_version(agent.id, agent.active_version)
    if version is None:
        raise _agent_not_found()
    return version


async def list_messages(conversation_id: str, limit: int = 40) -> list[MessageDoc]:
    """The newest ``limit`` messages, returned oldest → newest (single-field order by ``created_at``)."""
    query = (
        get_db()
        .collection(Col.CONVERSATIONS)
        .document(conversation_id)
        .collection(Col.MESSAGES)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    docs = [MessageDoc.model_validate(snap.to_dict()) async for snap in query.stream()]
    docs.reverse()
    return docs


async def get_conversation(conversation_id: str) -> ConversationDoc | None:
    if not conversation_id or "/" in conversation_id:
        return None
    snap = await get_db().collection(Col.CONVERSATIONS).document(conversation_id).get()
    return ConversationDoc.model_validate(snap.to_dict()) if snap.exists else None


async def get_scoped_conversation(
    conversation_id: str, *, agent_id: str, visitor_uid: str | None
) -> ConversationDoc:
    """Anti-IDOR scoping (SPEC §6.1): the conversation must exist, belong to ``agent_id`` and (on
    visitor routes) to ``visitor_uid``. Visitor routes → 404 ``session_not_owned``; owner routes → 404
    ``not_found``."""
    conv = await get_conversation(conversation_id)
    if visitor_uid is not None:
        if conv is None or conv.agent_id != agent_id or conv.visitor_uid != visitor_uid:
            raise ApiError(
                404, Codes.SESSION_NOT_OWNED, "This conversation is not available for this account."
            )
        return conv
    if conv is None or conv.agent_id != agent_id:
        raise ApiError(404, Codes.NOT_FOUND, "Conversation not found.")
    return conv
