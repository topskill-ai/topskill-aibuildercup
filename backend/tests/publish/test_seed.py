"""``scripts/seed_demo.py`` against the emulator (unique slug suffix per run)."""

from __future__ import annotations

import pytest

from scripts import seed_demo
from topskill.agents import deps
from topskill.repo import get_agent_by_slug, get_user, get_version
from topskill.testing.factories import unique

pytestmark = pytest.mark.emulator


async def test_seed_creates_demo_agents_idempotently(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []

    class _B:
        async def get_or_open_wallet(self, uid: str) -> int:
            opened.append(uid)
            return 10_000

    monkeypatch.setattr(deps, "billing", lambda: _B())
    monkeypatch.setattr(seed_demo, "billing", lambda: _B())
    suffix = unique("s")
    owner = unique("seed_")
    args = seed_demo.parse_args(
        ["--owner-uid", owner, "--slug-suffix", suffix, "--skip-knowledge", "--owner-name", "Ana Silva"]
    )
    results = await seed_demo.run(args)
    assert [r["outcome"] for r in results] == ["created"] * 3
    assert owner in opened

    user = await get_user(owner)
    assert user is not None and user.role == "creator" and user.display_name == "Ana Silva"

    visa = await get_agent_by_slug(f"visa-advisor-{suffix}")
    resume = await get_agent_by_slug(f"resume-reviewer-{suffix}")
    contract = await get_agent_by_slug(f"contract-reviewer-{suffix}")
    assert visa and resume and contract
    assert (
        visa.pricing.price_cents == 100
        and visa.pricing.free_rounds == 2
        and visa.pricing.a2a_price_cents == 200
    )
    assert resume.pricing.a2a_price_cents == 200
    assert contract.pricing.a2a_price_cents == 600 and contract.pricing.a2a_enabled
    assert visa.avatar_id and resume.avatar_id and contract.avatar_id
    assert visa.config.record_schema is not None
    assert {f.key for f in visa.config.record_schema.fields} >= {
        "nationality",
        "destination_country",
        "budget_eur",
    }
    assert visa.creator_name == "Ana Silva" and contract.creator_name == "Priya Raman"
    version = await get_version(visa.id, 1)
    assert version is not None and version.skills[0].slug == "visa-advisor"
    assert [f.path for f in version.skills[0].files] == ["SKILL.md", "references/document-checklist.md"]

    again = await seed_demo.run(args)
    assert [r["outcome"] for r in again] == ["exists"] * 3

    updated = await seed_demo.run(seed_demo.parse_args([*map(str, _argv(owner, suffix)), "--update"]))
    assert [r["outcome"] for r in updated] == ["updated"] * 3
    visa2 = await get_agent_by_slug(f"visa-advisor-{suffix}")
    assert visa2 is not None and visa2.active_version == 2 and not visa2.config_dirty


async def test_seed_refuses_someone_elses_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    class _B:
        async def get_or_open_wallet(self, uid: str) -> int:
            return 0

    monkeypatch.setattr(deps, "billing", lambda: _B())
    monkeypatch.setattr(seed_demo, "billing", lambda: _B())
    suffix = unique("s")
    await seed_demo.run(seed_demo.parse_args(_argv(unique("seed_"), suffix)))
    with pytest.raises(SystemExit):
        await seed_demo.run(seed_demo.parse_args(_argv(unique("seed_"), suffix)))


def _argv(owner: str, suffix: str) -> list[str]:
    return ["--owner-uid", owner, "--slug-suffix", suffix, "--skip-knowledge"]
