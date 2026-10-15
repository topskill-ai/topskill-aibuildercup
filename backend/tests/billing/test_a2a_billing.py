"""A2A billing: pre-flight (§8.6 steps 4–6), reservation T8 and settlement T3 on the emulator."""

from __future__ import annotations

import asyncio
import secrets

import pytest

from tests.billing.helpers import balance, doc, ledger, make_mandate, stats
from topskill import billing
from topskill.billing.a2a_billing import reserve_a2a_call
from topskill.billing.money import a2a_call_id
from topskill.db import Col, get_db
from topskill.errors import ApiError
from topskill.testing.factories import make_agent, make_user, open_wallet

pytestmark = pytest.mark.emulator


async def _setup(*, a2a_price: int = 200, **agent_overrides):
    owner = await make_user("creator", display_name="Olivia Owner")
    payer = await make_user(display_name="Acme Buyer")
    pricing = {"a2a_price_cents": a2a_price, **agent_overrides.pop("pricing", {})}
    agent = await make_agent(owner, title="Visa Advisor", pricing=pricing, **agent_overrides)
    await billing.get_or_open_wallet(payer.uid)
    return owner, payer, agent


async def _reserve(mandate, agent, price=None, message_id=None) -> str:
    price = agent.pricing.a2a_price_cents if price is None else price
    return await reserve_a2a_call(
        mandate, agent, message_id or secrets.token_hex(8), "Plan a work visa", price
    )


async def test_reserve_and_settle_completed_call() -> None:
    owner, payer, agent = await _setup()
    mandate = await make_mandate(payer)
    price = await billing.preflight_a2a(agent, mandate)
    assert price == 200
    call_id = await _reserve(mandate, agent, price, message_id="msg-1")
    assert call_id == a2a_call_id(mandate.mandate_id, "msg-1")
    call = await doc(Col.A2A_CALLS, call_id)
    assert call["status"] == "running" and call["price_cents"] == 200 and call["owner_uid"] == owner.uid
    assert call["request_preview"] == "Plan a work visa" and call["finished_at"] is None
    assert (await doc(Col.MANDATES, mandate.mandate_id))["reserved_cents"] == 200

    result = await billing.settle_a2a_call(
        call_id=call_id, succeeded=True, model="gemini-3.8-flash", answer_preview="x" * 500
    )
    assert result.status == "completed" and result.charged_cents == 200 and result.creator_share_cents == 160
    assert result.payer_balance_cents == 9800 and result.mandate_spent_cents == 200
    m = await doc(Col.MANDATES, mandate.mandate_id)
    assert m["reserved_cents"] == 0 and m["spent_cents"] == 200 and m["calls"] == 1
    call = await doc(Col.A2A_CALLS, call_id)
    assert call["status"] == "completed" and call["charged_cents"] == 200
    assert call["creator_share_cents"] == 160 and len(call["answer_preview"]) == 300
    assert call["finished_at"] is not None and call["model"] == "gemini-3.8-flash"
    assert await balance(payer.uid) == 9800 and await balance(owner.uid) == 10160

    pay = (await ledger(payer.uid))[f"{payer.uid}__a2a_{call_id}"]
    assert pay["category"] == "a2a_payment" and pay["kind"] == "DEBIT" and pay["amount_cents"] == -200
    assert pay["billing_mode"] == "a2a" and pay["mandate_id"] == mandate.mandate_id
    assert pay["a2a_call_id"] == call_id and pay["counterparty_uid"] == owner.uid
    assert pay["memo"] == "Visa Advisor · A2A call" and pay["conversation_id"] is None
    inc = (await ledger(owner.uid))[f"{owner.uid}__a2a_income_{call_id}"]
    assert inc["category"] == "a2a_income" and inc["amount_cents"] == 160
    assert inc["request_id"] == f"a2a_income_{call_id}" and inc["balance_after_cents"] == 10160
    st = await stats(agent.id)
    assert st["a2a_calls"] == 1 and st["a2a_gross_cents"] == 200 and st["creator_earned_cents"] == 160


async def test_failed_call_releases_reservation_and_charges_nothing() -> None:
    owner, payer, agent = await _setup()
    mandate = await make_mandate(payer)
    call_id = await _reserve(mandate, agent)
    result = await billing.settle_a2a_call(
        call_id=call_id, succeeded=False, model=None, answer_preview="", error_code="agent_run_failed"
    )
    assert result.status == "failed" and result.charged_cents == 0 and result.mandate_spent_cents == 0
    m = await doc(Col.MANDATES, mandate.mandate_id)
    assert m["reserved_cents"] == 0 and m["spent_cents"] == 0 and m["calls"] == 0
    call = await doc(Col.A2A_CALLS, call_id)
    assert call["status"] == "failed" and call["error_code"] == "agent_run_failed"
    assert await balance(payer.uid) == 10000 and await balance(owner.uid) == 10000
    assert set(await ledger(payer.uid)) == {f"{payer.uid}__signup"}
    assert (await stats(agent.id))["a2a_calls"] == 0


async def test_settle_is_idempotent() -> None:
    _owner, payer, agent = await _setup()
    mandate = await make_mandate(payer)
    call_id = await _reserve(mandate, agent)
    first = await billing.settle_a2a_call(call_id=call_id, succeeded=True, model=None, answer_preview="ok")
    # the failure paths call settle unconditionally after the executor already settled
    again = await billing.settle_a2a_call(
        call_id=call_id, succeeded=False, model=None, answer_preview="", error_code="upstream_error"
    )
    assert first.status == "completed" and again.status == "duplicate"
    assert again.charged_cents == 200 and again.creator_share_cents == 160
    assert again.payer_balance_cents == 9800 and again.mandate_spent_cents == 200
    m = await doc(Col.MANDATES, mandate.mandate_id)
    assert m["reserved_cents"] == 0 and m["spent_cents"] == 200 and m["calls"] == 1
    assert (await doc(Col.A2A_CALLS, call_id))["status"] == "completed"
    assert await balance(payer.uid) == 9800
    unknown = await billing.settle_a2a_call(call_id="nope", succeeded=True, model=None, answer_preview="")
    assert unknown.status == "duplicate" and unknown.charged_cents == 0


async def test_price_snapshot_is_charged_even_after_live_price_changes() -> None:
    _owner, payer, agent = await _setup(a2a_price=200)
    mandate = await make_mandate(payer)
    call_id = await _reserve(mandate, agent)
    await get_db().collection(Col.AGENTS).document(agent.id).update({"pricing.a2a_price_cents": 900})
    result = await billing.settle_a2a_call(call_id=call_id, succeeded=True, model=None, answer_preview="")
    assert result.charged_cents == 200 and result.creator_share_cents == 160


async def test_charge_is_capped_by_payer_balance_and_never_negative() -> None:
    _owner, payer, agent = await _setup(a2a_price=200)
    mandate = await make_mandate(payer)
    call_id = await _reserve(mandate, agent)
    await open_wallet(payer.uid, 50)  # balance dropped after the reservation
    result = await billing.settle_a2a_call(call_id=call_id, succeeded=True, model=None, answer_preview="")
    assert result.charged_cents == 50 and result.creator_share_cents == 40 and result.payer_balance_cents == 0
    await open_wallet(payer.uid, 0)
    call2 = await _reserve(mandate, agent)
    zero = await billing.settle_a2a_call(call_id=call2, succeeded=True, model=None, answer_preview="")
    assert zero.status == "completed" and zero.charged_cents == 0 and zero.creator_share_cents == 0
    assert await balance(payer.uid) == 0
    m = await doc(Col.MANDATES, mandate.mandate_id)
    assert m["spent_cents"] == 50 and m["reserved_cents"] == 0 and m["calls"] == 2
    assert not any(
        r["category"] == "a2a_payment" and r["amount_cents"] == 0 for r in (await ledger(payer.uid)).values()
    )


async def test_replay_of_the_same_message_is_rejected() -> None:
    _owner, payer, agent = await _setup()
    mandate = await make_mandate(payer)
    await _reserve(mandate, agent, message_id="same")
    with pytest.raises(ApiError) as err:
        await _reserve(mandate, agent, message_id="same")
    assert err.value.status == 402 and err.value.code == "mandate_replay"
    assert (await doc(Col.MANDATES, mandate.mandate_id))["reserved_cents"] == 200


async def test_parallel_reservations_only_reserve_what_fits() -> None:
    owner, payer, agent = await _setup(a2a_price=500)
    mandate = await make_mandate(payer, max_amount_cents=500, budget_cents=1500)
    results = await asyncio.gather(*(_reserve(mandate, agent) for _ in range(5)), return_exceptions=True)
    reserved = [r for r in results if isinstance(r, str)]
    rejected = [r for r in results if not isinstance(r, str)]
    assert len(reserved) == 3 and len(rejected) == 2
    assert all(isinstance(r, ApiError) and r.code == "mandate_budget_exhausted" for r in rejected)
    m = await doc(Col.MANDATES, mandate.mandate_id)
    assert m["reserved_cents"] == 1500 and m["spent_cents"] == 0
    # pre-flight sees the reservations too
    with pytest.raises(ApiError) as err:
        await billing.preflight_a2a(agent, mandate)
    assert err.value.code == "mandate_budget_exhausted"

    # settle concurrently: two complete, one fails → spent 1000, reserved back to 0
    outcomes = await asyncio.gather(
        *(
            billing.settle_a2a_call(call_id=cid, succeeded=i % 2 == 0, model=None, answer_preview="")
            for i, cid in enumerate(reserved)
        )
    )
    assert sorted(o.status for o in outcomes) == ["completed", "completed", "failed"]
    m = await doc(Col.MANDATES, mandate.mandate_id)
    assert m["reserved_cents"] == 0 and m["spent_cents"] == 1000 and m["calls"] == 2
    assert m["spent_cents"] + m["reserved_cents"] <= m["budget_cents"]
    assert await balance(payer.uid) == 9000 and await balance(owner.uid) == 10800
    # the freed budget can be used again
    assert await billing.preflight_a2a(agent, mandate) == 500


async def test_preflight_rejections() -> None:
    owner, payer, agent = await _setup(a2a_price=600)
    mandate = await make_mandate(payer, max_amount_cents=500)
    with pytest.raises(ApiError) as err:
        await billing.preflight_a2a(agent, mandate)
    assert err.value.status == 402 and err.value.code == "mandate_exceeds_max"
    assert err.value.detail["price_cents"] == 600

    owner, payer, agent = await _setup(a2a_price=300)
    spent = await make_mandate(
        payer, max_amount_cents=500, budget_cents=1000, spent_cents=600, reserved_cents=200
    )
    with pytest.raises(ApiError) as err:
        await billing.preflight_a2a(agent, spent)
    assert err.value.code == "mandate_budget_exhausted"

    mandate = await make_mandate(payer)
    await open_wallet(payer.uid, 100)
    with pytest.raises(ApiError) as err:
        await billing.preflight_a2a(agent, mandate)
    assert err.value.code == "payer_insufficient_points"
    assert err.value.detail["required_cents"] == 300 and err.value.detail["balance_cents"] == 100

    await open_wallet(payer.uid, 10000)
    await open_wallet(owner.uid, 0)
    with pytest.raises(ApiError) as err:
        await billing.preflight_a2a(agent, mandate)
    assert err.value.code == "insufficient_points" and err.value.detail == {
        "reason": "owner",
        "slug": agent.slug,
    }

    with pytest.raises(ApiError) as err:
        await billing.preflight_a2a(agent.model_copy(update={"status": "paused"}), mandate)
    assert err.value.status == 410 and err.value.code == "agent_offline"
    disabled = agent.model_copy(update={"pricing": agent.pricing.model_copy(update={"a2a_enabled": False})})
    with pytest.raises(ApiError) as err:
        await billing.preflight_a2a(disabled, mandate)
    assert err.value.status == 404 and err.value.code == "agent_not_found"


async def test_preflight_opens_owner_wallet() -> None:
    owner = await make_user("creator")
    payer = await make_user()
    agent = await make_agent(owner, open_owner_wallet=False)
    mandate = await make_mandate(payer)
    assert await balance(owner.uid) is None
    assert await billing.preflight_a2a(agent, mandate) == 200
    assert await balance(owner.uid) == 10000 and await balance(payer.uid) == 10000


async def test_owner_wallet_created_inside_settlement() -> None:
    owner = await make_user("creator")
    payer = await make_user()
    agent = await make_agent(owner, open_owner_wallet=False)
    await billing.get_or_open_wallet(payer.uid)
    mandate = await make_mandate(payer)
    call_id = await _reserve(mandate, agent)
    result = await billing.settle_a2a_call(call_id=call_id, succeeded=True, model=None, answer_preview="")
    assert result.creator_share_cents == 160 and await balance(owner.uid) == 10160
    assert f"{owner.uid}__signup" in await ledger(owner.uid)


async def test_owner_calling_own_agent_pays_nothing() -> None:
    owner, _payer, agent = await _setup()
    mandate = await make_mandate(owner)
    price = await billing.preflight_a2a(agent, mandate)
    assert price == 0
    call_id = await _reserve(mandate, agent, price)
    result = await billing.settle_a2a_call(call_id=call_id, succeeded=True, model=None, answer_preview="")
    assert result.status == "completed" and result.charged_cents == 0
    assert await balance(owner.uid) == 10000
    m = await doc(Col.MANDATES, mandate.mandate_id)
    assert m["calls"] == 1 and m["spent_cents"] == 0
