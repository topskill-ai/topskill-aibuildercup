"""A2A call billing: pre-flight (§8.6 steps 4–6), settlement T3 and the T8 reservation.

Price flow: the live ``pricing.a2a_price_cents`` is read at pre-flight, snapshotted into
``a2a_calls/{id}.price_cents`` by the reservation (T8) and settlement (T3) charges only that snapshot.
Invariant kept on every mandate: ``spent_cents + reserved_cents <= budget_cents``.
"""

from __future__ import annotations

import logging
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.async_transaction import AsyncTransaction

from topskill.billing import money
from topskill.billing.service import (
    TX_MAX_ATTEMPTS,
    balance_of,
    credit_owner,
    get_many,
    get_or_open_wallet,
    ledger_ref,
    owner_unavailable,
    stats_ref,
    wallet_ref,
)
from topskill.db import Col, get_db, utcnow
from topskill.domain import A2ACharge, AgentDoc, VerifiedMandate
from topskill.errors import ApiError, Codes

log = logging.getLogger(__name__)

ANSWER_PREVIEW_CHARS = 300
REQUEST_PREVIEW_CHARS = 120


def _mandate_ref(mandate_id: str):
    return get_db().collection(Col.MANDATES).document(mandate_id)


def _call_ref(call_id: str):
    return get_db().collection(Col.A2A_CALLS).document(call_id)


def _budget_exhausted(mandate_id: str, remaining: int, price: int) -> ApiError:
    return ApiError(
        402,
        Codes.MANDATE_BUDGET_EXHAUSTED,
        "The mandate's remaining budget does not cover this call.",
        {"mandate_id": mandate_id, "remaining_cents": max(0, remaining), "price_cents": price},
    )


def _mandate_counters(data: dict[str, Any]) -> tuple[int, int, int]:
    """(budget, spent, reserved) in cents."""
    return (
        int(data.get("budget_cents", 0)),
        int(data.get("spent_cents", 0)),
        int(data.get("reserved_cents", 0)),
    )


# --------------------------------------------------------------------------- pre-flight


async def preflight_a2a(agent: AgentDoc, mandate: VerifiedMandate) -> int:
    """Live A2A price in cents after the money checks of §8.6 steps 4–6 (advisory; T8 is authoritative).

    Opens the owner's (and payer's) wallet. An owner calling their own agent pays 0.
    """
    if agent.deleted or agent.status != "active":
        raise ApiError(
            410, Codes.AGENT_OFFLINE, "This agent is temporarily unavailable.", {"slug": agent.slug}
        )
    if not agent.pricing.a2a_enabled:
        raise ApiError(404, Codes.AGENT_NOT_FOUND, "This agent does not accept A2A calls.")
    is_owner = mandate.payer_uid == agent.owner_uid
    price = 0 if is_owner else agent.pricing.a2a_price_cents

    # 4. per-call cap
    if price > mandate.max_amount_cents:
        raise ApiError(
            402,
            Codes.MANDATE_EXCEEDS_MAX,
            "This agent's price is above the mandate's per-call maximum.",
            {"price_cents": price, "max_amount_cents": mandate.max_amount_cents, "slug": agent.slug},
        )

    # 5. remaining budget (spent + reserved)
    snap = await _mandate_ref(mandate.mandate_id).get()
    if not snap.exists:
        raise ApiError(402, Codes.MANDATE_INVALID, "The payment mandate is not valid.")
    budget, spent, reserved = _mandate_counters(snap.to_dict() or {})
    if spent + reserved + price > budget:
        raise _budget_exhausted(mandate.mandate_id, budget - spent - reserved, price)

    # 6. wallets
    owner_balance = await get_or_open_wallet(agent.owner_uid)
    payer_balance = owner_balance if is_owner else await get_or_open_wallet(mandate.payer_uid)
    if payer_balance < price:
        raise ApiError(
            402,
            Codes.PAYER_INSUFFICIENT_POINTS,
            "The paying account does not have enough credits for this call.",
            {"required_cents": price, "balance_cents": payer_balance, "slug": agent.slug},
        )
    if owner_balance <= 0:
        raise owner_unavailable(agent)
    return price


# --------------------------------------------------------------------------- T8 reservation


async def reserve_a2a_call(
    mandate: VerifiedMandate, agent: AgentDoc, message_id: str, request_preview: str, price_cents: int
) -> str:
    """Transaction T8 (§5.4, §8.6 step 7): reserve ``price_cents`` on the mandate and create the call doc.

    Reference implementation of ``a2a.reserve_call``: 402 ``mandate_replay`` when the message was already
    paid for; 402 ``mandate_budget_exhausted`` when ``spent + reserved + price > budget``. Returns the
    ``call_id``. With N parallel calls on one mandate only the ones that fit the budget are reserved.
    """
    call_id = money.a2a_call_id(mandate.mandate_id, message_id)
    call_ref = _call_ref(call_id)
    mandate_ref = _mandate_ref(mandate.mandate_id)

    @firestore.async_transactional
    async def _reserve(tx: AsyncTransaction) -> str:
        snaps = await get_many(tx, [call_ref, mandate_ref])
        call_snap = snaps.get(call_ref.path)
        mandate_snap = snaps.get(mandate_ref.path)
        if call_snap is not None and call_snap.exists:
            raise ApiError(402, Codes.MANDATE_REPLAY, "This message was already sent with this mandate.")
        if mandate_snap is None or not mandate_snap.exists:
            raise ApiError(402, Codes.MANDATE_INVALID, "The payment mandate is not valid.")
        budget, spent, reserved = _mandate_counters(mandate_snap.to_dict() or {})
        if spent + reserved + price_cents > budget:
            raise _budget_exhausted(mandate.mandate_id, budget - spent - reserved, price_cents)
        now = utcnow()
        tx.update(mandate_ref, {"reserved_cents": reserved + price_cents})
        tx.create(
            call_ref,
            {
                "id": call_id,
                "mandate_id": mandate.mandate_id,
                "payer_uid": mandate.payer_uid,
                "agent_id": agent.id,
                "agent_slug": agent.slug,
                "agent_title": agent.title,
                "owner_uid": agent.owner_uid,
                "message_id": message_id,
                "task_id": None,
                "status": "running",
                "price_cents": price_cents,
                "charged_cents": 0,
                "creator_share_cents": 0,
                "model": None,
                "error_code": None,
                "request_preview": request_preview[:REQUEST_PREVIEW_CHARS],
                "answer_preview": "",
                "created_at": now,
                "finished_at": None,
            },
        )
        return call_id

    return await _reserve(get_db().transaction(max_attempts=TX_MAX_ATTEMPTS))


# --------------------------------------------------------------------------- T3 settlement


async def settle_a2a_call(
    *,
    call_id: str,
    succeeded: bool,
    model: str | None,
    answer_preview: str,
    error_code: str | None = None,
) -> A2ACharge:
    """Transaction T3 (§8.6): settle a reserved call exactly once.

    Completed → charge ``min(call.price_cents snapshot, payer balance, mandate budget left)``, credit the
    owner's share, release the reservation, count the call. Anything else → release the reservation, mark
    the call ``failed``, charge nothing. A call that is not ``running`` returns ``status="duplicate"`` with
    the stored values and writes nothing. Never reads the agent: the live price is never used here.
    """
    db = get_db()
    call_ref = _call_ref(call_id)

    # Fast path without locks: the failure paths call this unconditionally after a normal settlement.
    pre = await call_ref.get()
    if pre.exists and (pre.to_dict() or {}).get("status") != "running":
        call = pre.to_dict() or {}
        snaps = await get_many(
            None, [_mandate_ref(str(call["mandate_id"])), wallet_ref(str(call["payer_uid"]))]
        )
        mandate_snap = snaps.get(_mandate_ref(str(call["mandate_id"])).path)
        _, spent, _ = _mandate_counters(mandate_snap.to_dict() or {} if mandate_snap else {})
        return A2ACharge(
            call_id=call_id,
            status="duplicate",
            charged_cents=int(call.get("charged_cents") or 0),
            creator_share_cents=int(call.get("creator_share_cents") or 0),
            payer_balance_cents=balance_of(snaps.get(wallet_ref(str(call["payer_uid"])).path)) or 0,
            mandate_spent_cents=spent,
        )

    @firestore.async_transactional
    async def _settle(tx: AsyncTransaction) -> A2ACharge:
        call_snap = await call_ref.get(transaction=tx)
        if not call_snap.exists:
            log.warning("a2a_settle_unknown_call call_id=%s", call_id)
            return A2ACharge(
                call_id=call_id,
                status="duplicate",
                charged_cents=0,
                creator_share_cents=0,
                payer_balance_cents=0,
                mandate_spent_cents=0,
            )
        call = call_snap.to_dict() or {}
        payer_uid = str(call["payer_uid"])
        owner_uid = str(call["owner_uid"])
        mandate_ref = _mandate_ref(str(call["mandate_id"]))
        payer_wallet = wallet_ref(payer_uid)
        owner_wallet = wallet_ref(owner_uid)
        snaps = await get_many(tx, [mandate_ref, payer_wallet, owner_wallet])
        mandate_snap = snaps.get(mandate_ref.path)
        mandate = (mandate_snap.to_dict() or {}) if mandate_snap is not None and mandate_snap.exists else None
        budget, spent, reserved = _mandate_counters(mandate or {})
        payer_balance = balance_of(snaps.get(payer_wallet.path))

        if call.get("status") != "running":
            return A2ACharge(
                call_id=call_id,
                status="duplicate",
                charged_cents=int(call.get("charged_cents") or 0),
                creator_share_cents=int(call.get("creator_share_cents") or 0),
                payer_balance_cents=payer_balance or 0,
                mandate_spent_cents=spent,
            )

        price = max(0, int(call.get("price_cents") or 0))
        now = utcnow()
        released = max(0, reserved - price)
        call_updates: dict[str, Any] = {"model": model, "finished_at": now}

        if not succeeded:
            if mandate is not None:
                tx.update(mandate_ref, {"reserved_cents": released})
            call_updates.update(
                {"status": "failed", "error_code": error_code or Codes.AGENT_RUN_FAILED, "charged_cents": 0}
            )
            tx.update(call_ref, call_updates)
            return A2ACharge(
                call_id=call_id,
                status="failed",
                charged_cents=0,
                creator_share_cents=0,
                payer_balance_cents=payer_balance or 0,
                mandate_spent_cents=spent,
            )

        is_owner = payer_uid == owner_uid
        budget_left = budget - spent if mandate is not None else 0
        charged = 0 if is_owner else max(0, min(price, payer_balance or 0, budget_left))
        share = money.creator_share(charged)
        title = str(call.get("agent_title") or "")
        common: dict[str, Any] = {
            "unit_price_cents": price,
            "billing_mode": "a2a",
            "agent_id": call.get("agent_id"),
            "agent_slug": call.get("agent_slug"),
            "agent_title": title,
            "mandate_id": call.get("mandate_id"),
            "a2a_call_id": call_id,
            "model": model,
            "memo": money.a2a_memo(title),
            "created_at": now,
        }
        new_payer_balance = payer_balance or 0
        if charged:
            new_payer_balance -= charged
            tx.update(payer_wallet, {"balance_cents": new_payer_balance, "updated_at": now})
            pay_id = money.a2a_payment_request_id(call_id)
            tx.create(
                ledger_ref(payer_uid, pay_id),
                money.ledger_doc(
                    uid=payer_uid,
                    request_id=pay_id,
                    category=money.A2A_PAYMENT,
                    amount_cents=-charged,
                    balance_after_cents=new_payer_balance,
                    counterparty_uid=owner_uid,
                    **common,
                ),
            )
            if share:
                owner_balance = credit_owner(
                    tx,
                    owner_uid=owner_uid,
                    owner_snap=snaps.get(owner_wallet.path),
                    share_cents=share,
                    now=now,
                )
                income_id = money.a2a_income_request_id(call_id)
                tx.create(
                    ledger_ref(owner_uid, income_id),
                    money.ledger_doc(
                        uid=owner_uid,
                        request_id=income_id,
                        category=money.A2A_INCOME,
                        amount_cents=share,
                        balance_after_cents=owner_balance,
                        counterparty_uid=payer_uid,
                        **common,
                    ),
                )
        new_spent = spent + charged
        if mandate is not None:
            calls = int(mandate.get("calls", 0)) + 1
            tx.update(mandate_ref, {"reserved_cents": released, "spent_cents": new_spent, "calls": calls})
        call_updates.update(
            {
                "status": "completed",
                "error_code": None,
                "charged_cents": charged,
                "creator_share_cents": share,
                "answer_preview": answer_preview[:ANSWER_PREVIEW_CHARS],
            }
        )
        tx.update(call_ref, call_updates)
        agent_id = call.get("agent_id")
        if agent_id:
            tx.set(
                stats_ref(str(agent_id)),
                {
                    "agent_id": agent_id,
                    "owner_uid": owner_uid,
                    "a2a_calls": firestore.Increment(1),
                    "a2a_gross_cents": firestore.Increment(charged),
                    "creator_earned_cents": firestore.Increment(share),
                    "last_activity_at": now,
                },
                merge=True,
            )
        return A2ACharge(
            call_id=call_id,
            status="completed",
            charged_cents=charged,
            creator_share_cents=share,
            payer_balance_cents=new_payer_balance,
            mandate_spent_cents=new_spent,
        )

    charge = await _settle(db.transaction(max_attempts=TX_MAX_ATTEMPTS))
    log.info(
        "a2a_call_settled call_id=%s status=%s charged_cents=%d share_cents=%d error_code=%s",
        call_id,
        charge.status,
        charge.charged_cents,
        charge.creator_share_cents,
        error_code if not succeeded else None,
    )
    return charge
