"""In-process turn locks, concurrency limit and abort registry (SPEC §10.4 steps 3 and 5).

Correct because the service runs as exactly one Cloud Run instance (SPEC §2 decision 1); the multi-instance
replacements are described in SPEC §16. Everything here runs on the event loop thread only, so plain
sets/dicts are race-free (no awaits between check and update).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from topskill.config import get_settings
from topskill.errors import ApiError, Codes

# One account may run at most this many turns at once (across conversations and agents), so a single
# account cannot hold every process-wide slot or race several paid rounds past the balance check.
MAX_TURNS_PER_USER = 3

_busy_conversations: set[str] = set()
_active_turns = 0
_turns_by_uid: dict[str, int] = {}
_uid_of_conversation: dict[str, str] = {}


@dataclass
class AbortEntry:
    event: asyncio.Event
    owner_uid: str
    agent_id: str


_aborts: dict[tuple[str, str], AbortEntry] = {}


def acquire_turn(conversation_id: str, uid: str | None = None) -> None:
    """Per-conversation lock (409 ``turn_in_progress``) + per-account limit of
    :data:`MAX_TURNS_PER_USER` running turns + process-wide limit (both 429 ``rate_limited``).
    The caller MUST call :func:`release_turn` exactly once afterwards."""
    global _active_turns
    if conversation_id in _busy_conversations:
        raise ApiError(409, Codes.TURN_IN_PROGRESS, "A reply is still being written in this conversation.")
    if uid is not None and _turns_by_uid.get(uid, 0) >= MAX_TURNS_PER_USER:
        raise ApiError(
            429,
            Codes.RATE_LIMITED,
            "You have too many replies being written at once. Please wait for one to finish.",
            {"retry_after_s": 5},
        )
    if _active_turns >= get_settings().max_concurrent_turns:
        raise ApiError(
            429,
            Codes.RATE_LIMITED,
            "Too many conversations right now. Please try again.",
            {"retry_after_s": 5},
        )
    _busy_conversations.add(conversation_id)
    _active_turns += 1
    if uid is not None:
        _turns_by_uid[uid] = _turns_by_uid.get(uid, 0) + 1
        _uid_of_conversation[conversation_id] = uid


def release_turn(conversation_id: str) -> None:
    global _active_turns
    if conversation_id in _busy_conversations:
        _busy_conversations.discard(conversation_id)
        _active_turns = max(0, _active_turns - 1)
        uid = _uid_of_conversation.pop(conversation_id, None)
        if uid is not None:
            left = _turns_by_uid.get(uid, 0) - 1
            if left > 0:
                _turns_by_uid[uid] = left
            else:
                _turns_by_uid.pop(uid, None)


def is_busy(conversation_id: str) -> bool:
    return conversation_id in _busy_conversations


def active_turns() -> int:
    return _active_turns


def register_abort(conversation_id: str, turn_id: str, *, owner_uid: str, agent_id: str) -> asyncio.Event:
    event = asyncio.Event()
    _aborts[(conversation_id, turn_id)] = AbortEntry(event=event, owner_uid=owner_uid, agent_id=agent_id)
    return event


def unregister_abort(conversation_id: str, turn_id: str) -> None:
    _aborts.pop((conversation_id, turn_id), None)


def request_abort(conversation_id: str, turn_id: str, *, uid: str, agent_id: str) -> bool:
    """Set the abort flag of a running turn; False (without reason) when not found / not owner / other
    agent / already set."""
    entry = _aborts.get((conversation_id, turn_id))
    if entry is None or entry.owner_uid != uid or entry.agent_id != agent_id or entry.event.is_set():
        return False
    entry.event.set()
    return True


def acquire_conversation(conversation_id: str, uid: str) -> None:
    """Hold a conversation without a process-wide turn slot (a voice call, SPEC §18.1): the
    per-conversation lock (409) and the per-account limit (429) of :func:`acquire_turn`, but not
    ``MAX_CONCURRENT_TURNS`` (voice has its own cap, ``VOICE_MAX_SESSIONS``), so open calls never
    starve text turns. Release with :func:`release_conversation`."""
    if conversation_id in _busy_conversations:
        raise ApiError(409, Codes.TURN_IN_PROGRESS, "A reply is still being written in this conversation.")
    if _turns_by_uid.get(uid, 0) >= MAX_TURNS_PER_USER:
        raise ApiError(
            429,
            Codes.RATE_LIMITED,
            "You have too many replies being written at once. Please wait for one to finish.",
            {"retry_after_s": 5},
        )
    _busy_conversations.add(conversation_id)
    _turns_by_uid[uid] = _turns_by_uid.get(uid, 0) + 1
    _uid_of_conversation[conversation_id] = uid


def release_conversation(conversation_id: str) -> None:
    if conversation_id in _busy_conversations:
        _busy_conversations.discard(conversation_id)
        uid = _uid_of_conversation.pop(conversation_id, None)
        if uid is not None:
            left = _turns_by_uid.get(uid, 0) - 1
            if left > 0:
                _turns_by_uid[uid] = left
            else:
                _turns_by_uid.pop(uid, None)
