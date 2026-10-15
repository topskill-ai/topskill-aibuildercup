"""Shared helpers for publish emulator tests."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import httpx

from tests.conftest import dev_headers
from topskill.testing.factories import unique

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SAMPLE_SKILL_DIR = FIXTURES / "skills" / "sample-skill"


def creator() -> tuple[str, dict[str, str]]:
    uid = unique("cr_")
    return uid, dev_headers(uid, "creator")


def visitor() -> tuple[str, dict[str, str]]:
    uid = unique("vi_")
    return uid, dev_headers(uid, "visitor")


def folder_files(path: Path = SAMPLE_SKILL_DIR) -> list[tuple[str, tuple[str, bytes, str]]]:
    """Multipart parts like a browser folder upload (``webkitRelativePath`` names)."""
    parts = []
    for file in sorted(path.rglob("*")):
        if file.is_file():
            rel = f"{path.name}/{file.relative_to(path).as_posix()}"
            parts.append(("files", (rel, file.read_bytes(), "text/markdown")))
    return parts


def folder_zip(path: Path = SAMPLE_SKILL_DIR) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(path.rglob("*")):
            if file.is_file():
                zf.write(file, f"{path.name}/{file.relative_to(path).as_posix()}")
    return buf.getvalue()


async def upload_skill(client: httpx.AsyncClient, headers: dict[str, str]) -> dict:
    resp = await client.post("/api/skills", files=folder_files(), headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def create_body(skill_ids: list[str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "skill_ids": skill_ids,
        "title": "Travel Insurance Helper",
        "tagline": "Pick the right travel cover.",
        "description": "Explains cover levels.",
        "greeting": "Hi! Where are you travelling?",
        "input_placeholder": "Describe your trip",
        "starter_questions": [{"label": "Which cover?", "prompt": "Which cover do I need?"}],
        "capabilities": [{"icon": "shield-check", "name": "Cover levels", "description": "Explains levels."}],
        "accent_color": "emerald",
        "pricing": {
            "mode": "per_round",
            "price_cents": 100,
            "free_rounds": 2,
            "a2a_enabled": True,
            "a2a_price_cents": 200,
        },
        "config": {"soul": "You are a travel insurance helper.", "web_search_enabled": True},
    }
    body.update(overrides)
    return body


async def publish(client: httpx.AsyncClient, headers: dict[str, str], **overrides: Any) -> dict:
    skill = await upload_skill(client, headers)
    resp = await client.post("/api/agents", json=create_body([skill["id"]], **overrides), headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()
