"""In-process registry of live voice sessions (SPEC §18.1 "Limits").

One live session per account: a new session replaces the account's previous one (which ends with reason
``replaced``) so a reloaded tab never stays locked out. A process-wide cap (``VOICE_MAX_SESSIONS``) gives
429 ``rate_limited``. Correct because the service runs as one Cloud Run instance (SPEC §2 decision 1).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from topskill.config import get_settings
from topskill.errors import ApiError, Codes

log = logging.getLogger(__name__)

REPLACE_WAIT_S = 10.0


class Replaceable(Protocol):
    finished: asyncio.Event

    def replace(self) -> None: ...


_by_uid: dict[str, Replaceable] = {}


def active_count() -> int:
    return len(_by_uid)


def get(uid: str) -> Replaceable | None:
    return _by_uid.get(uid)


async def claim(uid: str, session: Replaceable) -> None:
    """Register ``session`` as the account's only live session (ending previous ones first).

    Re-reads the slot after every wait: two calls of one account racing against an existing session
    both replace it, and the one that registers second replaces the first in turn."""
    deadline = asyncio.get_running_loop().time() + REPLACE_WAIT_S
    while (previous := _by_uid.get(uid)) is not None and previous is not session:
        log.info("voice_session_replaced uid=%s", uid)
        previous.replace()
        left = max(0.0, deadline - asyncio.get_running_loop().time())
        try:
            await asyncio.wait_for(previous.finished.wait(), timeout=left)
        except TimeoutError:
            log.warning("voice_session_replace_timeout uid=%s", uid)
        if _by_uid.get(uid) is previous:
            _by_uid.pop(uid, None)
    if uid not in _by_uid and len(_by_uid) >= get_settings().voice_max_sessions:
        raise ApiError(
            429,
            Codes.RATE_LIMITED,
            "Too many voice calls right now. Please try again in a minute or keep chatting by text.",
            {"retry_after_s": 30},
        )
    _by_uid[uid] = session


def release(uid: str, session: Replaceable) -> None:
    if _by_uid.get(uid) is session:
        _by_uid.pop(uid, None)
