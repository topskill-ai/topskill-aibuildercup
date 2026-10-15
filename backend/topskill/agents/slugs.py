"""Agent slugs (SPEC §8.7): format, reserved words, auto slugs and availability."""

from __future__ import annotations

import re
import secrets
from typing import Literal

from topskill.db import Col, get_db
from topskill.errors import ApiError, Codes
from topskill.skills.parser import slugify

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$")
RESERVED: frozenset[str] = frozenset(
    {
        "api",
        "v1",
        "admin",
        "dashboard",
        "studio",
        "wallet",
        "assistant",
        "login",
        "logout",
        "register",
        "auth",
        "health",
        "healthz",
        "a",
        "s",
        "u",
        "me",
        "a2a",
        "well-known",
        ".well-known",
        "static",
        "assets",
        "docs",
        "new",
        "settings",
        "topskill",
    }
)
AUTO_BASE_MAX = 50

SlugReason = Literal["slug_invalid", "slug_reserved", "slug_taken"]

_MESSAGES: dict[str, str] = {
    Codes.SLUG_INVALID: (
        "Use 3–60 lowercase letters, digits or hyphens; start and end with a letter or digit."
    ),
    Codes.SLUG_RESERVED: "This address is reserved. Please choose another one.",
    Codes.SLUG_TAKEN: "This address is already taken. Please choose another one.",
}


def normalize_slug(slug: str | None) -> str:
    return (slug or "").strip().lower()


def format_problem(slug: str) -> SlugReason | None:
    """Format/reserved check only (no I/O)."""
    if slug in RESERVED:
        return "slug_reserved"
    if not SLUG_RE.fullmatch(slug):
        return "slug_invalid"
    return None


def slug_error(reason: str) -> ApiError:
    return ApiError(400, reason, _MESSAGES.get(reason, "Invalid address."))


def auto_slug(title: str) -> str:
    """slugify(title) cut to 50 chars + ``-`` + 6 random hex; ``agent-<hex>`` when the base is < 3 chars."""
    base = slugify(title, max_len=AUTO_BASE_MAX)
    suffix = secrets.token_hex(3)
    if len(base) < 3:
        return f"agent-{suffix}"
    return f"{base}-{suffix}"


async def is_taken(slug: str) -> bool:
    snap = await get_db().collection(Col.SLUGS).document(slug).get()
    return snap.exists


async def check_slug(raw: str | None) -> dict:
    """``{slug, valid, available, reason}`` for ``GET /api/agents/slug-check``."""
    slug = normalize_slug(raw)
    reason = format_problem(slug)
    if reason is not None:
        return {"slug": slug, "valid": False, "available": False, "reason": reason}
    if await is_taken(slug):
        return {"slug": slug, "valid": True, "available": False, "reason": "slug_taken"}
    return {"slug": slug, "valid": True, "available": True, "reason": None}
