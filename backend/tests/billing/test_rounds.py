"""Round gate (§8.2) and settlement T2 (§8.5) against the Firestore emulator."""

from __future__ import annotations

import asyncio

import pytest

from tests.billing.helpers import Writer, balance, doc, ledger, new_request_id, settle, stats
from topskill import billing
from topskill.db import Col, get_db, new_id
from topskill.errors import ApiError
from topskill.testing.factories import make_agent, make_user, open_wallet

pytestmark = pytest.mark.emulator


async def _setup(*, open_visitor_wallet: bool = True, **agent_overrides):
    owner = await make_user("creator", display_name="Olivia Owner")
    visitor = await make_user(display_name="Ana Silva")
    agent = await make_agent(owner, **agent_overrides)
    if open_visitor_wallet:
        await billing.get_or_open_wallet(visitor.uid)
    return owner, visitor, agent


async def test_free_rounds_boundary_then_charged_with_share() -> None:
    owner, visitor, agent = await _setup(title="Visa Advisor")  # 1.00 per round, 2 free rounds
    cid = new_id("cnv")
    quotes, charges = [], []
    for _ in range(3):
        quotes.append(await billing.preflight_round(agent, visitor))
        charge, _ = await settle(billing, agent, visitor, conversation_id=cid)
        charges.append(charge)

    assert [q.round_index for q in quotes] == [1, 2, 3]
    assert [q.free for q in quotes] == [True, True, False] and quotes[2].charge_cents == 100
    assert [c.outcome for c in charges] == ["free", "free", "charged"]
    assert [c.round_index for c in charges] == [1, 2, 3]
    assert [c.free_rounds_left for c in charges] == [1, 0, 0]
    assert charges[2].charged_cents == 100 and charges[2].creator_share_cents == 80
    assert charges[2].balance_cents == 9900

    assert await balance(visitor.uid) == 9900
    assert await balance(owner.uid) == 10080
    usage = await doc(Col.USAGE, f"{agent.id}__{visitor.uid}")
    assert usage["rounds_used"] == 3 and usage["paid_rounds"] == 1

    rid = charges[2].request_id
    debit = (await ledger(visitor.uid))[f"{visitor.uid}__{rid}"]
    assert debit["category"] == "visitor_payment" and debit["kind"] == "DEBIT"
    assert debit["amount_cents"] == -100 and debit["balance_after_cents"] == 9900
    assert debit["unit_price_cents"] == 100 and debit["billing_mode"] == "per_round"
    assert debit["counterparty_uid"] == owner.uid and debit["round_index"] == 3
    assert debit["memo"] == "Visa Advisor · round 3" and debit["conversation_id"] == cid
    assert debit["agent_slug"] == agent.slug and debit["model"] == "gemini-3.8-flash"
    credit = (await ledger(owner.uid))[f"{owner.uid}__income_{rid}"]
    assert credit["category"] == "creator_income" and credit["kind"] == "CREDIT"
    assert credit["request_id"] == f"income_{rid}"
    assert credit["amount_cents"] == 80 and credit["balance_after_cents"] == 10080
    assert credit["counterparty_uid"] == visitor.uid
    assert credit["memo"] == "Visa Advisor · round 3 by Ana Silva"

    st = await stats(agent.id)
    assert st["rounds"] == 3 and st["paid_rounds"] == 1 and st["conversations"] == 1
    assert st["visitor_gross_cents"] == 100 and st["creator_earned_cents"] == 80
    assert st["last_activity_at"] is not None

    # the model message stores the charge the writer received
    msg = (
        await get_db()
        .collection(Col.CONVERSATIONS)
        .document(cid)
        .collection(Col.MESSAGES)
        .document(f"m_{rid}")
        .get()
    )
    assert msg.to_dict()["charge"] == {"charged_cents": 100, "free": False, "round_index": 3}


async def test_owner_rounds_are_free_and_not_counted() -> None:
    owner, _, agent = await _setup(pricing={"free_rounds": 0})
    quote = await billing.preflight_round(agent, owner)
    assert quote.is_owner and quote.free and quote.round_index is None and quote.charge_cents == 0
    charge, _ = await settle(billing, agent, owner)
    assert charge.outcome == "free" and charge.charged_cents == 0 and charge.round_index is None
    assert charge.free_rounds_left is None and charge.balance_cents == 10000
    assert await doc(Col.USAGE, f"{agent.id}__{owner.uid}") is None
    assert await balance(owner.uid) == 10000
    assert set(await ledger(owner.uid)) == {f"{owner.uid}__signup"}
    st = await stats(agent.id)
    assert st["rounds"] == 1 and st["paid_rounds"] == 0 and st["creator_earned_cents"] == 0


async def test_failed_rounds_are_not_counted_and_keep_free_rounds() -> None:
    _owner, visitor, agent = await _setup(pricing={"free_rounds": 1})
    cid = new_id("cnv")
    failed, writer = await settle(billing, agent, visitor, conversation_id=cid, succeeded=False)
    assert failed.outcome == "not_counted" and failed.charged_cents == 0 and failed.round_index is None
    assert failed.free_rounds_left == 1
    assert writer.calls[-1][1] is False  # conversation created by the failed round
    assert await doc(Col.USAGE, f"{agent.id}__{visitor.uid}") is None
    # the free round is still available
    ok, _ = await settle(billing, agent, visitor, conversation_id=cid)
    assert ok.outcome == "free" and ok.round_index == 1
    paid, _ = await settle(billing, agent, visitor, conversation_id=cid)
    assert paid.outcome == "charged" and paid.round_index == 2
    conv = await doc(Col.CONVERSATIONS, cid)
    assert conv["rounds_ok"] == 2 and conv["message_count"] == 6
    st = await stats(agent.id)
    assert st["rounds"] == 2 and st["conversations"] == 1 and st["paid_rounds"] == 1


async def test_share_floor_one_cent_price() -> None:
    owner, visitor, agent = await _setup(pricing={"price_cents": 1, "free_rounds": 0})
    charge, _ = await settle(billing, agent, visitor)
    assert charge.outcome == "charged" and charge.charged_cents == 1 and charge.creator_share_cents == 0
    assert await balance(visitor.uid) == 9999
    assert await balance(owner.uid) == 10000
    assert not any(r["category"] == "creator_income" for r in (await ledger(owner.uid)).values())
    st = await stats(agent.id)
    assert st["visitor_gross_cents"] == 1 and st["creator_earned_cents"] == 0 and st["paid_rounds"] == 1


async def test_balance_never_goes_negative() -> None:
    _owner, visitor, agent = await _setup(pricing={"price_cents": 100, "free_rounds": 0})
    await open_wallet(visitor.uid, 50)
    partial, _ = await settle(billing, agent, visitor)
    assert partial.outcome == "charged" and partial.charged_cents == 50 and partial.creator_share_cents == 40
    assert partial.balance_cents == 0 and await balance(visitor.uid) == 0
    empty, _ = await settle(billing, agent, visitor)  # settlement after the gate would have refused
    assert empty.outcome == "free" and empty.charged_cents == 0 and await balance(visitor.uid) == 0
    usage = await doc(Col.USAGE, f"{agent.id}__{visitor.uid}")
    assert usage["rounds_used"] == 2 and usage["paid_rounds"] == 1


async def test_concurrent_paid_rounds_never_overdraw() -> None:
    """Two tabs settle at once: the second one is under-charged, the balance never goes negative."""
    owner, visitor, agent = await _setup(pricing={"price_cents": 100, "free_rounds": 0})
    await open_wallet(visitor.uid, 150)
    results = await asyncio.gather(*(settle(billing, agent, visitor) for _ in range(2)))
    charged = sorted(c.charged_cents for c, _ in results)
    assert charged == [50, 100]
    assert await balance(visitor.uid) == 0
    assert await balance(owner.uid) == 10000 + 80 + 40


async def test_duplicate_settlement_is_a_no_op() -> None:
    owner, visitor, agent = await _setup(pricing={"free_rounds": 0})
    cid, rid = new_id("cnv"), new_request_id()
    first, _ = await settle(billing, agent, visitor, conversation_id=cid, request_id=rid)
    again, writer = await settle(billing, agent, visitor, conversation_id=cid, request_id=rid)
    assert first.outcome == "charged" and again.outcome == "duplicate"
    assert again.charged_cents == 100 and again.round_index == 1 and again.creator_share_cents == 80
    assert again.balance_cents == 9900
    assert writer.calls == []  # nothing written on the duplicate
    assert await balance(visitor.uid) == 9900 and await balance(owner.uid) == 10080
    conv = await doc(Col.CONVERSATIONS, cid)
    assert conv["message_count"] == 2
    st = await stats(agent.id)
    assert st["rounds"] == 1 and st["paid_rounds"] == 1


async def test_conversation_create_vs_increment() -> None:
    _owner, visitor, agent = await _setup(pricing={"mode": "free", "price_cents": 0})
    cid = new_id("cnv")
    _, w1 = await settle(billing, agent, visitor, conversation_id=cid, user_text="First question")
    _, w2 = await settle(billing, agent, visitor, conversation_id=cid, user_text="Second question")
    assert w1.calls[-1][1] is False and w2.calls[-1][1] is True
    conv = await doc(Col.CONVERSATIONS, cid)
    assert conv["title"] == "First question" and conv["rounds_ok"] == 2 and conv["message_count"] == 4
    # a second conversation counts once more
    await settle(billing, agent, visitor)
    st = await stats(agent.id)
    assert st["conversations"] == 2 and st["rounds"] == 3 and st["paid_rounds"] == 0


async def test_free_mode_never_charges() -> None:
    _owner, visitor, agent = await _setup(pricing={"mode": "free", "price_cents": 0, "free_rounds": 0})
    await open_wallet(visitor.uid, 0)
    quote = await billing.preflight_round(agent, visitor)
    assert quote.free and quote.round_index == 1
    charge, _ = await settle(billing, agent, visitor)
    assert charge.outcome == "free" and charge.free_rounds_left is None and charge.round_index == 1


async def test_settlement_reads_live_pricing_but_never_above_the_turn_start_price() -> None:
    _owner, visitor, agent = await _setup(pricing={"free_rounds": 0, "price_cents": 100})
    ref = get_db().collection(Col.AGENTS).document(agent.id)
    await ref.update({"pricing.price_cents": 60})
    charge, _ = await settle(billing, agent, visitor)  # `agent` is the doc the turn started with
    assert charge.charged_cents == 60 and charge.creator_share_cents == 48  # a cut applies at once
    await ref.update({"pricing.price_cents": 100_000})  # creator hikes the price mid-turn
    charge, _ = await settle(billing, agent, visitor)
    assert charge.charged_cents == 100 and charge.balance_cents == 10_000 - 60 - 100


async def test_free_to_paid_switch_mid_turn_is_free() -> None:
    _owner, visitor, agent = await _setup(pricing={"mode": "free", "free_rounds": 0, "price_cents": 0})
    ref = get_db().collection(Col.AGENTS).document(agent.id)
    await ref.update({"pricing.mode": "per_round", "pricing.price_cents": 500})
    charge, _ = await settle(billing, agent, visitor)
    assert charge.outcome == "free" and charge.charged_cents == 0


async def test_creator_balance_gate_runs_before_visitor_gate() -> None:
    owner, visitor, agent = await _setup(pricing={"free_rounds": 0}, owner_balance_cents=0)
    await open_wallet(visitor.uid, 0)
    with pytest.raises(ApiError) as err:
        await billing.preflight_round(agent, visitor)
    assert err.value.status == 402 and err.value.code == "insufficient_points"
    assert err.value.detail == {"reason": "owner", "slug": agent.slug}
    # also for the owner's own testing and for free agents
    with pytest.raises(ApiError) as own:
        await billing.preflight_round(agent, owner)
    assert own.value.code == "insufficient_points"


async def test_visitor_gate_after_free_rounds() -> None:
    _owner, visitor, agent = await _setup(pricing={"free_rounds": 1, "price_cents": 100})
    await open_wallet(visitor.uid, 40)
    quote = await billing.preflight_round(agent, visitor)  # round 1 is free: allowed with 0.40
    assert quote.free and quote.balance_cents == 40
    await settle(billing, agent, visitor)
    with pytest.raises(ApiError) as err:
        await billing.preflight_round(agent, visitor)
    assert err.value.status == 402 and err.value.code == "visitor_insufficient_points"
    assert err.value.detail == {
        "required_cents": 100,
        "balance_cents": 40,
        "price_cents": 100,
        "billing_mode": "per_round",
        "slug": agent.slug,
    }
    assert err.value.message == "You need 1.00 credits; you have 0.40."


async def test_preflight_opens_wallets_and_rejects_offline_agents() -> None:
    _owner, visitor, agent = await _setup(open_visitor_wallet=False)
    assert await balance(visitor.uid) is None
    quote = await billing.preflight_round(agent, visitor)
    assert quote.balance_cents == 10000 and await balance(visitor.uid) == 10000
    for overrides in ({"status": "paused"}, {"deleted": True}):
        with pytest.raises(ApiError) as err:
            await billing.preflight_round(agent.model_copy(update=overrides), visitor)
        assert err.value.status == 410 and err.value.code == "agent_offline"


async def test_owner_without_wallet_preflight_opens_it_and_is_credited() -> None:
    owner = await make_user("creator")
    visitor = await make_user()
    agent = await make_agent(owner, open_owner_wallet=False, pricing={"free_rounds": 0})
    assert await balance(owner.uid) is None
    await billing.preflight_round(agent, visitor)
    assert await balance(owner.uid) == 10000
    charge, _ = await settle(billing, agent, visitor)
    assert charge.outcome == "charged"
    assert await balance(owner.uid) == 10080


async def test_owner_wallet_created_inside_settlement_as_last_resort() -> None:
    owner = await make_user("creator")
    visitor = await make_user()
    agent = await make_agent(owner, open_owner_wallet=False, pricing={"free_rounds": 0})
    await billing.get_or_open_wallet(visitor.uid)
    charge, _ = await settle(billing, agent, visitor)  # no preflight: owner wallet still missing
    assert charge.outcome == "charged" and charge.creator_share_cents == 80
    assert await balance(owner.uid) == 10080
    rows = await ledger(owner.uid)
    assert rows[f"{owner.uid}__signup"]["category"] == "signup_grant"
    assert rows[f"{owner.uid}__signup"]["balance_after_cents"] == 10000
    income = rows[f"{owner.uid}__income_{charge.request_id}"]
    assert income["balance_after_cents"] == 10080


async def test_transaction_failure_persists_nothing() -> None:
    """§8.5: if the transaction fails at commit, nothing is persisted — including the queued messages."""
    owner, visitor, agent = await _setup(pricing={"free_rounds": 0})
    owner_before = await balance(owner.uid)
    stats_before = await stats(agent.id)
    cid = new_id("cnv")
    rid = new_request_id()
    # A pre-existing ledger row makes the visitor_payment ``tx.create`` fail with AlreadyExists at commit,
    # after the writer has queued its messages + conversation and billing its wallet/usage/stats writes.
    clash = get_db().collection(Col.LEDGER).document(f"{visitor.uid}__{rid}")
    await clash.set({"uid": visitor.uid, "request_id": rid, "category": "adjust", "amount_cents": 0})
    writer = Writer(agent, visitor, cid, rid)

    with pytest.raises(Exception) as excinfo:
        await billing.settle_round(
            agent=agent,
            payer=visitor,
            conversation_id=cid,
            request_id=rid,
            succeeded=True,
            model=None,
            write_messages=writer,
        )
    assert "exist" in str(excinfo.value).lower()
    assert writer.calls and writer.calls[-1][0].outcome == "charged"  # writes were queued

    conv_ref = get_db().collection(Col.CONVERSATIONS).document(cid)
    assert await doc(Col.CONVERSATIONS, cid) is None
    for msg_id in (f"u_{rid}", f"m_{rid}"):
        assert not (await conv_ref.collection(Col.MESSAGES).document(msg_id).get()).exists
    assert await doc(Col.USAGE, f"{agent.id}__{visitor.uid}") is None
    assert await balance(visitor.uid) == 10000 and await balance(owner.uid) == owner_before
    assert f"{owner.uid}__income_{rid}" not in await ledger(owner.uid)
    stats_after = await stats(agent.id)
    assert (stats_after or {}).get("rounds", 0) == (stats_before or {}).get("rounds", 0)
