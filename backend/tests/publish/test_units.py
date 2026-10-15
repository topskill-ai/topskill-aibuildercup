"""Slugs, draft post-validation (mocked model), avatars and starter questions (unit)."""

from __future__ import annotations

import io
import re

import pytest
from PIL import Image

from topskill import gemini
from topskill.agents import draft as drafts
from topskill.agents.avatar import build_prompt, to_webp
from topskill.agents.schemas import agent_urls, avatar_url, resolve_starter_questions
from topskill.agents.slugs import SLUG_RE, auto_slug, format_problem
from topskill.db import utcnow
from topskill.domain import AgentConfig, AgentDoc, Capability, Pricing, StarterQuestion
from topskill.errors import ApiError

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- slugs


@pytest.mark.parametrize(
    ("slug", "reason"),
    [
        ("visa-advisor", None),
        ("abc", None),
        ("a" * 60, None),
        ("ab", "slug_invalid"),
        ("a" * 61, "slug_invalid"),
        ("-abc", "slug_invalid"),
        ("abc-", "slug_invalid"),
        ("Abc", "slug_invalid"),
        ("ab_c", "slug_invalid"),
        ("studio", "slug_reserved"),
        ("a2a", "slug_reserved"),
        ("topskill", "slug_reserved"),
        ("api", "slug_reserved"),
    ],
)
def test_slug_rules(slug: str, reason: str | None) -> None:
    assert format_problem(slug) == reason


def test_auto_slug_shapes() -> None:
    slug = auto_slug("Visa Advisor")
    assert re.fullmatch(r"visa-advisor-[0-9a-f]{6}", slug)
    assert auto_slug("Visa Advisor") != slug  # random suffix always added
    assert re.fullmatch(r"agent-[0-9a-f]{6}", auto_slug("日本"))
    assert re.fullmatch(r"agent-[0-9a-f]{6}", auto_slug("A!"))
    long = auto_slug("Extremely Long Agent Title " * 5)
    assert SLUG_RE.fullmatch(long) and len(long) <= 57
    assert SLUG_RE.fullmatch(auto_slug("São Paulo Relocation Coach"))


# --------------------------------------------------------------------------- draft


def _draft(**overrides) -> drafts.DraftModel:
    data = {
        "title": '"Visa Advisor"',
        "tagline": "Work visas " * 20,
        "description": "Helps with visas.",
        "soul": "You are a visa advisor.",
        "greeting": "Hi!\n\nI   plan visas. " + "word " * 80,
        "input_placeholder": "Describe your case " * 5,
        "starter_questions": [
            {"label": "Plan my visa", "prompt": "Plan a visa for Germany."},
            {"label": "Documents", "prompt": "Which documents?"},
            {"label": "Costs " + "x" * 80, "prompt": "What does it cost?"},
            {"label": "Extra", "prompt": "Extra question"},
        ],
        "capabilities": [
            {"icon": "plane", "name": "Routes", "description": "Compares routes."},
            {"icon": "clipboard-list", "name": "Documents " * 5, "description": "Lists documents."},
            {"icon": "calculator", "name": "Budget", "description": "Costs."},
        ],
        "accent_color": "blue",
        "pricing": {"mode": "per_round", "price_cents": 5000, "free_rounds": 9, "rationale": "r" * 300},
        "record_fields": [
            {
                "key": "nationality",
                "label": "Nationality",
                "type": "text",
                "description": "d",
                "required": True,
                "pii": False,
                "show_in_table": True,
            },
            {
                "key": "Bad Key",
                "label": "Bad",
                "type": "text",
                "description": "",
                "required": False,
                "pii": False,
                "show_in_table": False,
            },
            {
                "key": "destination",
                "label": "Destination",
                "type": "single_choice",
                "description": "",
                "options": None,  # choice without options → dropped
                "required": False,
                "pii": False,
                "show_in_table": False,
            },
            {
                "key": "budget",
                "label": "Budget " * 10,
                "type": "number",
                "description": "",
                "min": 10,
                "max": 1,  # min > max → range dropped
                "required": False,
                "pii": False,
                "show_in_table": True,
            },
            {
                "key": "nationality",  # duplicate key → dropped
                "label": "Again",
                "type": "text",
                "description": "",
                "required": False,
                "pii": False,
                "show_in_table": False,
            },
        ],
        "web_search_recommended": True,
        "avatar_prompt": "A friendly consultant " * 30,
    }
    data.update(overrides)
    return drafts.DraftModel.model_validate(data)


def test_post_validate_clips_and_normalises() -> None:
    out = drafts.post_validate(_draft(), "gemini-3.8-flash")
    assert out["title"] == "Visa Advisor"
    assert len(out["tagline"]) <= 80 and out["tagline"].endswith("…")
    assert "\n" not in out["greeting"] and "  " not in out["greeting"]
    assert len(out["greeting"].split()) <= 61 and out["greeting"].endswith("…")
    assert len(out["input_placeholder"].split()) <= 13
    assert len(out["starter_questions"]) == 3
    assert len(out["starter_questions"][2]["label"]) <= 60
    assert out["capabilities"][1]["name"].endswith("…") and len(out["capabilities"][1]["name"]) <= 30
    assert out["pricing"] == {
        "mode": "per_round",
        "price_cents": 1000,
        "free_rounds": 5,
        "rationale": out["pricing"]["rationale"],
    }
    assert len(out["pricing"]["rationale"]) <= 200
    keys = [f["key"] for f in out["record_fields"]]
    assert keys == ["nationality", "budget"]
    budget = out["record_fields"][1]
    assert budget["min"] is None and budget["max"] is None and len(budget["label"]) <= 40
    assert len(out["avatar_prompt"]) <= 300
    assert out["model_used"] == "gemini-3.8-flash"


def test_post_validate_free_mode_and_min_price() -> None:
    out = drafts.post_validate(
        _draft(pricing={"mode": "per_round", "price_cents": 3, "free_rounds": -2, "rationale": ""}), "m"
    )
    assert out["pricing"]["price_cents"] == 50 and out["pricing"]["free_rounds"] == 0
    out = drafts.post_validate(
        _draft(pricing={"mode": "free", "price_cents": 300, "free_rounds": 1, "rationale": ""}), "m"
    )
    assert out["pricing"]["mode"] == "free" and out["pricing"]["price_cents"] == 0


def test_post_validate_fewer_than_three_questions_fails() -> None:
    questions = [{"label": "One", "prompt": "One?"}, {"label": "", "prompt": "blank label"}]
    with pytest.raises(ApiError) as exc:
        drafts.post_validate(_draft(starter_questions=questions), "m")
    assert exc.value.status == 502 and exc.value.code == "draft_failed"


def test_user_message_budget_and_omission() -> None:
    small = [drafts.SkillText(name="A", description="d", body="body " * 10)]
    msg = drafts.build_user_message(small)
    assert "SKILL.md:" in msg and "## Skill 1: A" in msg
    big = [drafts.SkillText(name=f"S{i}", description="x" * 400, body="y" * 50_000) for i in range(5)]
    msg = drafts.build_user_message(big)
    assert len(msg) <= drafts.USER_MESSAGE_MAX
    assert msg.count("SKILL.md:") == 5
    assert "x" * 301 not in msg  # descriptions clipped to 300
    # 80 skills' worth of headers leave < 200 chars per body → bodies omitted
    tight = [drafts.SkillText(name="N" * 100, description="D" * 300, body="z" * 1000) for _ in range(40)]
    assert "SKILL.md:" not in drafts.build_user_message(tight)


async def test_draft_from_texts_uses_chain_and_maps_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], str]] = []

    async def fake_structured(chain, contents, schema, **kwargs):
        calls.append((chain, kwargs.get("thinking")))
        assert schema is drafts.DraftModel and kwargs.get("system_instruction")
        return _draft(), chain[0]

    monkeypatch.setattr(gemini, "generate_structured", fake_structured)
    out = await drafts.draft_from_texts([drafts.SkillText("A", "d", "body")], "fast")
    assert calls[-1] == (gemini.model_chain("draft"), "low") and out["model_used"] == calls[-1][0][0]
    await drafts.draft_from_texts([drafts.SkillText("A", "d", "body")], "best")
    assert calls[-1][0] == gemini.model_chain("draft_pro")

    async def failing(*args, **kwargs):
        raise ApiError(502, "upstream_error", "boom")

    monkeypatch.setattr(gemini, "generate_structured", failing)
    with pytest.raises(ApiError) as exc:
        await drafts.draft_from_texts([drafts.SkillText("A", "d", "body")], "fast")
    assert exc.value.code == "draft_failed"


# --------------------------------------------------------------------------- avatars


def _image(fmt: str, size: tuple[int, int] = (640, 480), mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, (200, 30, 30) if mode == "RGB" else (200, 30, 30, 128)).save(buf, format=fmt)
    return buf.getvalue()


@pytest.mark.parametrize(("fmt", "mode"), [("PNG", "RGB"), ("PNG", "RGBA"), ("JPEG", "RGB"), ("WEBP", "RGB")])
def test_to_webp_center_crops_to_384(fmt: str, mode: str) -> None:
    out = to_webp(_image(fmt, mode=mode))
    with Image.open(io.BytesIO(out)) as img:
        assert img.format == "WEBP" and img.size == (384, 384)


@pytest.mark.parametrize("data", [_image("GIF"), b"not an image", b""])
def test_to_webp_rejects_other_types(data: bytes) -> None:
    with pytest.raises(ApiError) as exc:
        to_webp(data)
    assert exc.value.code == "unsupported_file_type"


def test_avatar_prompt_has_style_suffix() -> None:
    prompt = build_prompt("  A visa consultant. ", "emerald")
    assert prompt.startswith("A visa consultant. flat vector illustration")
    assert "emerald tones" in prompt and "no text, no letters" in prompt
    assert "blue tones" in build_prompt("x", None)


# --------------------------------------------------------------------------- derived fields


def _agent(**overrides) -> AgentDoc:
    now = utcnow()
    data = {
        "id": "agt_x",
        "owner_uid": "u",
        "slug": "visa-advisor",
        "title": "Visa Advisor",
        "pricing": Pricing(),
        "config": AgentConfig(soul="s", skill_ids=["skl_1"]),
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return AgentDoc.model_validate(data)


def test_resolve_starter_questions() -> None:
    assert resolve_starter_questions(_agent(starter_questions=[])) == []
    explicit = [StarterQuestion(label="L", prompt="P")]
    assert resolve_starter_questions(_agent(starter_questions=explicit)) == explicit
    defaults = resolve_starter_questions(
        _agent(
            capabilities=[Capability(icon="plane", name="Route assessment", description="Compares routes.")]
        )
    )
    assert len(defaults) == 3
    assert defaults[0].label == "Route assessment" and "route assessment" in defaults[0].prompt
    assert "Visa Advisor" in defaults[1].prompt
    assert len(resolve_starter_questions(_agent())) == 3


def test_urls() -> None:
    assert avatar_url(None) is None
    assert avatar_url("av_1").endswith("/api/public/avatars/av_1")
    urls = agent_urls("visa-advisor")
    assert urls["public_page"].endswith("/a/visa-advisor")
    assert urls["a2a_card"].endswith("/a2a/visa-advisor/.well-known/agent-card.json")
    assert urls["a2a_endpoint"].endswith("/a2a/visa-advisor")


def test_skill_wire_created_at_is_never_null() -> None:
    from datetime import UTC, datetime

    from topskill.agents.schemas import SkillOut
    from topskill.skills.service import skill_to_wire

    legacy = skill_to_wire({"id": "skl_1"})  # a doc written before created_at existed
    assert isinstance(legacy["created_at"], str) and legacy["created_at"].endswith("Z")
    SkillOut.model_validate(legacy)
    stamped = skill_to_wire({"id": "skl_2", "created_at": datetime(2026, 10, 1, tzinfo=UTC)})
    assert stamped["created_at"].startswith("2026-10-01T00:00:00")
