"""Transaction T8: replay protection and budget reservation (SPEC §8.6 step 7)."""

from __future__ import annotations

import asyncio

import pytest

from tests.a2a.conftest import issue_mandate
from topskill.a2a import call_id_for, reserve_call, verify_token
from topskill.db import Col, get_db
from topskill.errors import ApiError
from topskill.testing.factories import make_agent, make_user, unique

pytestmark = pytest.mark.emulator


async def _mandate_data(mandate_id: str) -> dict:
    return (await get_db().collection(Col.MANDATES).document(mandate_id).get()).to_dict() or {}


async def test_reserve_creates_running_call_with_price_snapshot(client) -> None:
    owner = await make_user("creator")
    agent = await make_agent(owner, pricing={"a2a_price_cents": 200}, title="Visa Advisor")
    payer = unique("u_")
    mandate, token = await issue_mandate(client, payer)
    verified = await verify_token(token, agent.slug)

    call_id = await reserve_call(verified, agent, "msg-1", "Need a work visa plan", 200)
    assert call_id == call_id_for(mandate["id"], "msg-1") and len(call_id) == 32
    call = (await get_db().collection(Col.A2A_CALLS).document(call_id).get()).to_dict()
    assert call["status"] == "running" and call["price_cents"] == 200 and call["charged_cents"] == 0
    assert call["owner_uid"] == owner.uid and call["agent_title"] == "Visa Advisor"
    assert call["payer_uid"] == payer and call["request_preview"] == "Need a work visa plan"
    assert (await _mandate_data(mandate["id"]))["reserved_cents"] == 200

    # replay of the same message → mandate_replay, nothing reserved twice
    with pytest.raises(ApiError) as exc:
        await reserve_call(verified, agent, "msg-1", "again", 200)
    assert exc.value.status == 402 and exc.value.code == "mandate_replay"
    assert (await _mandate_data(mandate["id"]))["reserved_cents"] == 200


async def test_reserve_rejects_over_max_and_over_budget(client) -> None:
    owner = await make_user("creator")
    agent = await make_agent(owner)
    payer = unique("u_")
    mandate, token = await issue_mandate(client, payer, max_amount_cents=500, budget_cents=700)
    verified = await verify_token(token, agent.slug)

    with pytest.raises(ApiError) as exc:
        await reserve_call(verified, agent, "m-over", "x", 600)
    assert exc.value.code == "mandate_exceeds_max"

    await reserve_call(verified, agent, "m-1", "x", 500)
    with pytest.raises(ApiError) as exc:
        await reserve_call(verified, agent, "m-2", "x", 300)
    assert exc.value.code == "mandate_budget_exhausted"
    assert exc.value.detail == {"price_cents": 300, "remaining_cents": 200}
    await reserve_call(verified, agent, "m-3", "x", 200)
    assert (await _mandate_data(mandate["id"]))["reserved_cents"] == 700


async def test_parallel_reservations_only_reserve_what_fits(client) -> None:
    owner = await make_user("creator")
    agent = await make_agent(owner)
    payer = unique("u_")
    mandate, token = await issue_mandate(client, payer, max_amount_cents=300, budget_cents=1000)
    verified = await verify_token(token, agent.slug)

    results = await asyncio.gather(
        *(reserve_call(verified, agent, f"par-{i}", "x", 300) for i in range(6)), return_exceptions=True
    )
    ok = [r for r in results if isinstance(r, str)]
    errors = [r for r in results if isinstance(r, BaseException)]
    assert len(ok) == 3, results
    assert all(isinstance(e, ApiError) and e.code == "mandate_budget_exhausted" for e in errors), errors
    assert (await _mandate_data(mandate["id"]))["reserved_cents"] == 900


async def test_reserve_rechecks_revocation(client) -> None:
    owner = await make_user("creator")
    agent = await make_agent(owner)
    payer = unique("u_")
    mandate, token = await issue_mandate(client, payer)
    verified = await verify_token(token, agent.slug)
    await get_db().collection(Col.MANDATES).document(mandate["id"]).update({"status": "revoked"})
    with pytest.raises(ApiError) as exc:
        await reserve_call(verified, agent, "m-rev", "x", 100)
    assert exc.value.code == "mandate_revoked"
