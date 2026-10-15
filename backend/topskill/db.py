"""Firestore access: client, collection names, ids and clock (SPEC §5, §14.2)."""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime

from google.cloud import firestore

from topskill.config import get_settings


class Col:
    """Collection names (SPEC §5.1)."""

    USERS = "users"
    WALLETS = "wallets"
    LEDGER = "ledger"
    AGENTS = "agents"
    VERSIONS = "versions"
    AGENT_STATS = "agent_stats"
    SLUGS = "slugs"
    SKILLS = "skills"
    KNOWLEDGE = "knowledge"
    KNOWLEDGE_CHUNKS = "knowledge_chunks"
    CONVERSATIONS = "conversations"
    MESSAGES = "messages"
    USAGE = "usage"
    RECORDS = "records"
    MANDATES = "mandates"
    A2A_CALLS = "a2a_calls"
    RATE_LIMITS = "rate_limits"


_clients: dict[int, firestore.AsyncClient] = {}
_fallback_client: firestore.AsyncClient | None = None


def _make_client() -> firestore.AsyncClient:
    # FIRESTORE_EMULATOR_HOST in the process env is honoured by the client itself.
    return firestore.AsyncClient(project=get_settings().firestore_project)


def get_db() -> firestore.AsyncClient:
    """The Firestore ``AsyncClient`` (``project=FIRESTORE_PROJECT``).

    One client per running event loop: in production (and in tests, which use one session loop) this is a
    singleton; it never lets a gRPC channel cross event loops.
    """
    global _fallback_client
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        if _fallback_client is None:
            _fallback_client = _make_client()
        return _fallback_client
    key = id(loop)
    client = _clients.get(key)
    if client is None:
        client = _make_client()
        _clients[key] = client
    return client


def reset_db_clients() -> None:
    """Forget cached clients (tests only)."""
    global _fallback_client
    _clients.clear()
    _fallback_client = None


def utcnow() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """``f"{prefix}_{20 lowercase hex}"`` (SPEC conventions)."""
    return f"{prefix}_{secrets.token_hex(10)}"
