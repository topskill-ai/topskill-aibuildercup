"""Directory ranking against the seeded demo listings (SPEC §6.4, §9.4): copies of a seeded agent
published later (e.g. the wizard beat of the demo) must not push a seeded specialist out of the
Enterprise Assistant's discovery list."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scripts.seed_demo import DEMO_AGENTS
from topskill.a2a import assistant, directory
from topskill.domain import AgentDoc

pytestmark = pytest.mark.unit

TASK = (
    "Our new hire from Brazil needs a work visa plan, a resume review and a quick check of the offer letter"
)
NOW = datetime(2026, 10, 15, tzinfo=UTC)


def _doc(slug: str, listing: dict, pricing: dict, minutes: int, **over) -> AgentDoc:
    return AgentDoc.model_validate(
        {
            **listing,
            "id": f"agt_{slug}",
            "owner_uid": "owner",
            "slug": slug,
            "pricing": pricing,
            "config": {"soul": "x", "model": "gemini-3.8-flash", "skill_ids": ["sk"]},
            "created_at": NOW + timedelta(minutes=minutes),
            "updated_at": NOW,
            **over,
        }
    )


def _seeded() -> list[AgentDoc]:
    return [_doc(s.slug, s.listing, s.pricing, i) for i, s in enumerate(DEMO_AGENTS)]


def _visa_copy(title: str, minutes: int) -> AgentDoc:
    visa = DEMO_AGENTS[0]
    return _doc(
        f"copy-{minutes}", visa.listing, {**visa.pricing, "a2a_price_cents": 100}, minutes, title=title
    )


async def _search(monkeypatch: pytest.MonkeyPatch, agents: list[AgentDoc]) -> list[str]:
    newest_first = sorted(agents, key=lambda a: a.created_at, reverse=True)

    async def candidates() -> list[AgentDoc]:
        return newest_first

    monkeypatch.setattr(directory, "_candidates", candidates)
    found = await directory.search(TASK, limit=assistant.DISCOVER_TOP_N, fallback=True)
    return [a.slug for a in found]


async def test_seeded_specialists_survive_a_newer_differently_titled_copy(monkeypatch) -> None:
    slugs = await _search(monkeypatch, [*_seeded(), _visa_copy("Work Visa Advisor", 60)])
    assert {"visa-advisor", "resume-reviewer", "contract-reviewer"} <= set(slugs), slugs


async def test_same_title_copies_collapse_to_the_oldest(monkeypatch) -> None:
    slugs = await _search(
        monkeypatch, [*_seeded(), _visa_copy("Visa Advisor", 60), _visa_copy("visa  advisor", 61)]
    )
    assert "visa-advisor" in slugs and not any(s.startswith("copy-") for s in slugs), slugs
    assert directory.dedupe_titles([_visa_copy("Visa Advisor", 9), *_seeded()[:1]])[0].slug == "visa-advisor"


def test_model_facing_amounts_are_credits() -> None:
    assert [assistant.credits(c) for c in (0, 5, 100, 250, 600)] == ["0.00", "0.05", "1.00", "2.50", "6.00"]
    assert "cents" not in assistant.ASSISTANT_PROMPT.replace("never mention cents", "")


def test_clip_does_not_leave_punctuation_before_the_ellipsis() -> None:
    assert assistant._clip("Work visas in Brazil. More text follows here", 22) == "Work visas in Brazil…"
    assert assistant._clip("short", 22) == "short"
