"""Conversation history for stateless turns (SPEC §10.4 "History").

Our Firestore messages are the source of truth. Each turn reads the last messages and seeds a fresh
``InMemorySessionService`` session; ADK session storage is never persisted.
"""

from __future__ import annotations

import re

from google.adk.events import Event
from google.adk.sessions import BaseSessionService, Session
from google.genai import types

from topskill import repo
from topskill.domain import HistoryItem, MessageDoc

HISTORY_MESSAGES = 40
MESSAGE_MAX_CHARS = 8_000
TOTAL_MAX_CHARS = 24_000

# Citation markers of an earlier answer: ``[3]``, ``[3, 4]``, ``[2-5]``, ``[1][2]`` (not a Markdown link
# ``[1](url)``). They only point at that turn's Sources, so the model must not see (and copy) them.
_MARKER_RE = re.compile(r"[ \t]?\[\d{1,3}(?:\s*[,\u2013-]\s*\d{1,3})*\](?!\()")


_CODE_RE = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)


def strip_citation_markers(text: str) -> str:
    """Model text without ``[n]`` citation markers (they refer to sources of their own turn only); code
    spans and fences are left as they are."""
    parts = _CODE_RE.split(text)
    return "".join(p if i % 2 else _MARKER_RE.sub("", p) for i, p in enumerate(parts))


def trim_history(messages: list[MessageDoc]) -> list[HistoryItem]:
    """Oldest → newest; skip empty text; clip each message; keep ≤ TOTAL_MAX_CHARS counted from the
    newest message; start with a user message. Aborted model text is kept (the visitor saw it). Model
    text loses its ``[n]`` citation markers: they point at that turn's sources, and a model that sees
    them copies markers into later answers that cite nothing."""
    picked: list[HistoryItem] = []
    total = 0
    for msg in reversed(messages):
        text = msg.text.strip()
        if msg.role == "model":
            text = strip_citation_markers(text).strip()
        if not text:
            continue
        text = text[:MESSAGE_MAX_CHARS]
        if total + len(text) > TOTAL_MAX_CHARS:
            break
        total += len(text)
        picked.append(HistoryItem(role=msg.role, text=text))
    picked.reverse()
    while picked and picked[0].role != "user":
        picked.pop(0)
    return picked


async def load_history(conversation_id: str) -> list[HistoryItem]:
    messages = await repo.list_messages(conversation_id, limit=HISTORY_MESSAGES)
    return trim_history(messages)


async def seed_session(
    session_service: BaseSessionService,
    *,
    app_name: str,
    user_id: str,
    session_id: str,
    history: list[HistoryItem],
    agent_name: str,
) -> Session:
    """Create the session and append the history (model turns are authored by the agent name)."""
    session = await session_service.create_session(app_name=app_name, user_id=user_id, session_id=session_id)
    for i, item in enumerate(history):
        await session_service.append_event(
            session,
            Event(
                invocation_id=f"seed-{i}",
                author="user" if item.role == "user" else agent_name,
                content=types.Content(role=item.role, parts=[types.Part(text=item.text)]),
            ),
        )
    return session
