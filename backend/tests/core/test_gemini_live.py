"""Live smoke tests against Vertex AI.

Run manually: ``uv run pytest -m live tests/core/test_gemini_live.py``.
"""

from __future__ import annotations

import pytest
from google.genai import types
from pydantic import BaseModel, Field

from topskill import gemini

pytestmark = pytest.mark.live


class Listing(BaseModel):
    title: str = Field(description="2-6 words")
    starter_questions: list[str] = Field(description="exactly 3 questions")


async def test_live_generate_structured_draft_chain() -> None:
    parsed, model = await gemini.generate_structured(
        gemini.model_chain("draft"),
        "Draft a marketplace listing for an AI agent that helps people plan a Brazil work visa.",
        Listing,
        thinking="low",
    )
    assert model in gemini.model_chain("draft")
    assert parsed.title and len(parsed.starter_questions) >= 1


@pytest.mark.parametrize("model", ["gemini-3.8-flash", "gemini-3-flash-preview"])
async def test_live_portable_and_level_thinking_accepted(model: str) -> None:
    client = gemini.get_genai_client()
    for cfg in (gemini.portable_thinking_config("low"), gemini.thinking_config(model, "low")):
        resp = await client.aio.models.generate_content(
            model=model,
            contents="Reply with the single word: ok",
            config=types.GenerateContentConfig(thinking_config=cfg),
        )
        assert resp.text and "ok" in resp.text.lower()


async def test_live_generate_image() -> None:
    data, mime = await gemini.generate_image(
        "Flat vector illustration avatar of a friendly visa consultant, soft blue background, no text"
    )
    assert mime.startswith("image/") and len(data) > 1000
