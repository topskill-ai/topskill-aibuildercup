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

_busy_conversations: set[str] = set()
_active_turns = 0


@dataclass
class AbortEntry:
    event: asyncio.Event
    owner_uid: str
    agent_id: str


_aborts: dict[tuple[str, str], AbortEntry] = {}


def acquire_turn(conversation_id: str) -> None:
    """Per-conversation lock (409 ``turn_in_progress``) + process-wide limit (429 ``rate_limited``).
    The caller MUST call :func:`release_turn` exactly once afterwards."""
    global _active_turns
    if conversation_id in _busy_conversations:
        raise ApiError(409, Codes.TURN_IN_PROGRESS, "A reply is still being written in this conversation.")
    if _active_turns >= get_settings().max_concurrent_turns:
        raise ApiError(
            429,
            Codes.RATE_LIMITED,
            "Too many conversations right now. Please try again.",
            {"retry_after_s": 5},
        )
    _busy_conversations.add(conversation_id)
    _active_turns += 1


def release_turn(conversation_id: str) -> None:
    global _active_turns
    if conversation_id in _busy_conversations:
        _busy_conversations.discard(conversation_id)
        _active_turns = max(0, _active_turns - 1)


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
