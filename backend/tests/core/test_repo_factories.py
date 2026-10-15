from __future__ import annotations

import pytest

from topskill.db import Col, get_db
from topskill.errors import ApiError
from topskill.repo import (
    get_active_version,
    get_agent,
    get_agent_by_slug,
    get_conversation,
    get_owned_agent,
    get_public_agent,
    get_scoped_conversation,
    get_user,
    list_messages,
)
from topskill.storage import get_blob_store
from topskill.testing.factories import (
    make_agent,
    make_conversation,
    make_skill,
    make_user,
    make_version,
    open_wallet,
)

pytestmark = pytest.mark.emulator


async def test_make_agent_writes_every_doc() -> None:
    owner = await make_user("creator")
    agent = await make_agent(owner, title="Visa Advisor", pricing={"free_rounds": 3})
    assert agent.pricing.free_rounds == 3 and agent.pricing.price_cents == 100
    assert (await get_agent(agent.id)) == agent
    assert (await get_agent_by_slug(agent.slug)) == agent
    version = await get_active_version(agent)
    assert version.n == 1 and version.title == "Visa Advisor" and len(version.skills) == 1
    skill_body = await get_blob_store().get(version.skills[0].gcs_prefix + "SKILL.md")
    assert skill_body.startswith(b"---")
    stats = (await get_db().collection(Col.AGENT_STATS).document(agent.id).get()).to_dict()
    assert stats["owner_uid"] == owner.uid and stats["conversations"] == 0
    wallet = (await get_db().collection(Col.WALLETS).document(owner.uid).get()).to_dict()
    assert wallet["balance_cents"] == 10_000


async def test_make_agent_without_owner_wallet() -> None:
    owner = await make_user("creator")
    await make_agent(owner, open_owner_wallet=False)
    assert not (await get_db().collection(Col.WALLETS).document(owner.uid).get()).exists


async def test_open_wallet_and_users() -> None:
    user = await make_user("visitor", display_name="Ana Silva")
    assert user.display_name == "Ana Silva" and user.role == "visitor"
    assert await open_wallet(user.uid, 4200) == 4200
    ledger = (await get_db().collection(Col.LEDGER).document(f"{user.uid}__signup").get()).to_dict()
    assert ledger["category"] == "signup_grant" and ledger["amount_cents"] == 4200
    stored = await get_user(user.uid)
    assert stored is not None and stored.display_name == "Ana Silva"


async def test_owned_and_public_agent_rules() -> None:
    owner = await make_user("creator")
    other = await make_user("creator")
    agent = await make_agent(owner)
    assert (await get_owned_agent(agent.id, owner)).id == agent.id
    with pytest.raises(ApiError) as err:
        await get_owned_agent(agent.id, other)
    assert err.value.status == 404 and err.value.code == "agent_not_found"
    with pytest.raises(ApiError) as err:
        await get_owned_agent("agt_missing", owner)
    assert err.value.code == "agent_not_found"
    assert (await get_public_agent(agent.slug)).id == agent.id
    with pytest.raises(ApiError) as err:
        await get_public_agent("no-such-slug-here")
    assert err.value.status == 404

    paused = await make_agent(owner, status="paused")
    with pytest.raises(ApiError) as err:
        await get_public_agent(paused.slug)
    assert err.value.status == 410 and err.value.code == "agent_offline"
    deleted = await make_agent(owner, deleted=True, listed=False)
    with pytest.raises(ApiError) as err:
        await get_public_agent(deleted.slug)
    assert err.value.status == 410
    with pytest.raises(ApiError):
        await get_owned_agent(deleted.id, owner)


async def test_make_version_activates() -> None:
    owner = await make_user("creator")
    agent = await make_agent(owner)
    v2 = await make_version(agent, note="second")
    refreshed = await get_agent(agent.id)
    assert refreshed is not None and refreshed.active_version == 2 and refreshed.version_count == 2
    assert (await get_active_version(refreshed)).note == v2.note == "second"


async def test_skill_with_extra_files() -> None:
    owner = await make_user("creator")
    ref = await make_skill(owner, extra_files={"references/checklist.md": b"- passport"})
    assert [f.path for f in ref.files] == ["SKILL.md", "references/checklist.md"]
    assert await get_blob_store().get(ref.gcs_prefix + "references/checklist.md") == b"- passport"


async def test_conversation_scoping_and_messages() -> None:
    owner = await make_user("creator")
    visitor = await make_user()
    intruder = await make_user()
    agent_a = await make_agent(owner)
    agent_b = await make_agent(owner)
    conv = await make_conversation(agent_a, visitor, turns=[("q1", "a1"), ("q2", "a2"), ("q3", "a3")])
    assert (await get_conversation(conv.id)) == conv

    ok = await get_scoped_conversation(conv.id, agent_id=agent_a.id, visitor_uid=visitor.uid)
    assert ok.id == conv.id
    for agent_id, visitor_uid in ((agent_b.id, visitor.uid), (agent_a.id, intruder.uid), (agent_a.id, "x")):
        with pytest.raises(ApiError) as err:
            await get_scoped_conversation(conv.id, agent_id=agent_id, visitor_uid=visitor_uid)
        assert err.value.status == 404 and err.value.code == "session_not_owned"
    with pytest.raises(ApiError) as err:
        await get_scoped_conversation("cnv_unknown", agent_id=agent_a.id, visitor_uid=visitor.uid)
    assert err.value.code == "session_not_owned"
    # owner routes
    assert (await get_scoped_conversation(conv.id, agent_id=agent_a.id, visitor_uid=None)).id == conv.id
    with pytest.raises(ApiError) as err:
        await get_scoped_conversation(conv.id, agent_id=agent_b.id, visitor_uid=None)
    assert err.value.status == 404 and err.value.code == "not_found"

    messages = await list_messages(conv.id)
    assert [m.text for m in messages] == ["q1", "a1", "q2", "a2", "q3", "a3"]
    newest = await list_messages(conv.id, limit=2)
    assert [m.text for m in newest] == ["q3", "a3"]
