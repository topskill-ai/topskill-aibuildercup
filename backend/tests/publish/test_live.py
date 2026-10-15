"""Live smoke tests against Vertex AI (never in CI).

Run manually: ``uv run pytest -m live tests/publish/test_live.py``.
"""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from PIL import Image

from topskill import gemini
from topskill.agents import draft as drafts
from topskill.agents.avatar import build_prompt, to_webp
from topskill.skills.parser import build_package

pytestmark = pytest.mark.live

DEMO = Path(__file__).resolve().parents[2] / "scripts" / "demo_data"


def _skill_text(name: str) -> drafts.SkillText:
    folder = DEMO / name
    entries = [
        (f"{name}/{p.relative_to(folder).as_posix()}", p.read_bytes())
        for p in folder.rglob("*")
        if p.is_file()
    ]
    pkg = build_package(entries)
    return drafts.SkillText(name=pkg.name, description=pkg.description, body=pkg.body)


@pytest.mark.parametrize("quality", ["fast", "best"])
async def test_live_draft_visa_advisor(quality: str) -> None:
    started = time.monotonic()
    out = await drafts.draft_from_texts([_skill_text("visa-advisor")], quality)  # type: ignore[arg-type]
    elapsed = time.monotonic() - started
    chain = gemini.model_chain("draft_pro" if quality == "best" else "draft")
    assert out["model_used"] in chain
    assert 0 < len(out["title"]) <= 40
    assert len(out["starter_questions"]) == 3
    assert 1 <= len(out["capabilities"]) <= 4
    assert out["soul"].lower().startswith("you")
    assert 50 <= out["pricing"]["price_cents"] <= 1000 or out["pricing"]["mode"] == "free"
    keys = [f["key"] for f in out["record_fields"]]
    assert len(keys) == len(set(keys)) <= 6
    print(f"\n[{quality}] {out['model_used']} {elapsed:.1f}s title={out['title']!r} fields={keys}")


async def test_live_avatar_generation() -> None:
    data, mime = await gemini.generate_image(
        build_prompt("A friendly visa consultant with a passport", "blue")
    )
    webp = to_webp(data)
    with Image.open(io.BytesIO(webp)) as img:
        assert img.size == (384, 384) and img.format == "WEBP"
    assert mime.startswith("image/")
