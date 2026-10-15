"""Chat endpoints (SPEC §6.4 "Chat").

``public_router`` (prefix ``/api/public``): visitor chat (SSE), abort, restore own conversation.
``router`` (prefix ``/api``): owner conversation list/detail [SHOULD].
Scoping (SPEC §6.1): a conversation is only ever loaded together with its agent and, on visitor routes,
its visitor; mismatches are 404 (``session_not_owned`` / ``not_found``), never 403.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, Query
from google.cloud import firestore
from pydantic import BaseModel, Field, field_validator

from topskill import repo
from topskill.auth import CurrentUser, get_current_user, require_creator
from topskill.chat import locks
from topskill.chat.deps import get_deps
from topskill.chat.runtime import TurnContext, start_turn
from topskill.config import get_settings
from topskill.db import Col, get_db, new_id
from topskill.domain import AgentDoc, ConversationDoc, iso_z, message_to_wire
from topskill.errors import ApiError, Codes
from topskill.pagination import clamp_limit, fetch_page
from topskill.sse import sse_response

log = logging.getLogger(__name__)

public_router = APIRouter(tags=["chat"])
router = APIRouter(tags=["chat"])

TURN_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"
RESTORE_MAX_MESSAGES = 100


class ChatRequest(BaseModel):
    conversation_id: str | None = Field(default=None, max_length=64)
    turn_id: str = Field(pattern=TURN_ID_PATTERN)
    message: str = Field(min_length=1, max_length=8000)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class AbortRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=64)
    turn_id: str = Field(min_length=1, max_length=64)


class AbortResponse(BaseModel):
    aborted: bool


def conversation_out(conv: ConversationDoc, messages: list) -> dict:
    """→ TS ``ConversationOut``."""
    return {
        "id": conv.id,
        "agent_slug": conv.agent_slug,
        "title": conv.title,
        "rounds_ok": conv.rounds_ok,
        "created_at": iso_z(conv.created_at),
        "updated_at": iso_z(conv.updated_at),
        "messages": [message_to_wire(m) for m in messages],
    }


@public_router.post("/agents/{slug}/chat")
async def chat(slug: str, body: ChatRequest, user: CurrentUser = Depends(get_current_user)):
    settings = get_settings()
    agent = await repo.get_public_agent(slug)
    version = await repo.get_active_version(agent)

    if body.conversation_id is not None:
        conv = await repo.get_scoped_conversation(
            body.conversation_id, agent_id=agent.id, visitor_uid=user.uid
        )
        if conv.rounds_ok >= settings.max_rounds_per_conversation:
            raise ApiError(
                400,
                Codes.CONVERSATION_LIMIT_REACHED,
                "This conversation is full. Please start a new one.",
                {"max_rounds": settings.max_rounds_per_conversation},
            )
        conversation_id, is_new = conv.id, False
    else:
        conversation_id, is_new = new_id("cnv"), True

    locks.acquire_turn(conversation_id, user.uid)
    released = False

    def release() -> None:
        nonlocal released
        if not released:
            released = True
            locks.release_turn(conversation_id)
            locks.unregister_abort(conversation_id, body.turn_id)

    try:
        await get_deps().billing.preflight_round(agent, user)
        ctx = TurnContext(
            agent=agent,
            version=version,
            user=user,
            conversation_id=conversation_id,
            conversation_is_new=is_new,
            turn_id=body.turn_id,
            request_id="turn_" + secrets.token_hex(16),
            message=body.message.strip(),
        )
        ctx.abort = locks.register_abort(conversation_id, body.turn_id, owner_uid=user.uid, agent_id=agent.id)
        frames = start_turn(ctx, on_finish=release)
    except BaseException:
        release()
        raise
    return sse_response(frames)


@public_router.post("/agents/{slug}/chat/abort", response_model=AbortResponse)
async def abort_chat(slug: str, body: AbortRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    agent = await repo.get_agent_by_slug(slug)
    if agent is None:
        return {"aborted": False}
    return {
        "aborted": locks.request_abort(body.conversation_id, body.turn_id, uid=user.uid, agent_id=agent.id)
    }


@public_router.get("/agents/{slug}/conversations/{conversation_id}")
async def get_my_conversation(
    slug: str, conversation_id: str, user: CurrentUser = Depends(get_current_user)
) -> dict:
    agent = await repo.get_agent_by_slug(slug)
    if agent is None:
        raise ApiError(404, Codes.SESSION_NOT_OWNED, "This conversation is not available for this account.")
    conv = await repo.get_scoped_conversation(conversation_id, agent_id=agent.id, visitor_uid=user.uid)
    messages = await repo.list_messages(conv.id, limit=RESTORE_MAX_MESSAGES)
    return conversation_out(conv, messages)


# --------------------------------------------------------------------------- owner routes [SHOULD]


async def _owned(agent_id: str, user: CurrentUser) -> AgentDoc:
    return await repo.get_owned_agent(agent_id, user)


@router.get("/agents/{agent_id}/conversations")
async def list_agent_conversations(
    agent_id: str,
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=100),
    user: CurrentUser = Depends(require_creator),
) -> dict:
    agent = await _owned(agent_id, user)
    col = get_db().collection(Col.CONVERSATIONS)
    query = col.where(filter=firestore.FieldFilter("agent_id", "==", agent.id))
    snaps, next_cursor = await fetch_page(
        query, collection=col, order_field="updated_at", cursor=cursor, limit=clamp_limit(limit)
    )
    names: dict[str, str] = {}
    items = []
    for snap in snaps:
        conv = ConversationDoc.model_validate(snap.to_dict())
        if conv.visitor_uid not in names:
            visitor = await repo.get_user(conv.visitor_uid)
            names[conv.visitor_uid] = visitor.display_name if visitor else "Visitor"
        items.append(
            {
                "id": conv.id,
                "visitor_name": names[conv.visitor_uid],
                "title": conv.title,
                "rounds_ok": conv.rounds_ok,
                "message_count": conv.message_count,
                "created_at": iso_z(conv.created_at),
                "updated_at": iso_z(conv.updated_at),
            }
        )
    return {"items": items, "next_cursor": next_cursor}


@router.get("/agents/{agent_id}/conversations/{conversation_id}")
async def get_agent_conversation(
    agent_id: str, conversation_id: str, user: CurrentUser = Depends(require_creator)
) -> dict:
    agent = await _owned(agent_id, user)
    conv = await repo.get_scoped_conversation(conversation_id, agent_id=agent.id, visitor_uid=None)
    messages = await repo.list_messages(conv.id, limit=RESTORE_MAX_MESSAGES)
    return conversation_out(conv, messages)
