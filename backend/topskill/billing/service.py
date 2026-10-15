"""Wallets, the round gate and round settlement (SPEC §5.4 T1/T2/T4, §8.1–§8.5).

Every transaction does all of its reads before any write (Firestore requirement) and runs through
``firestore.async_transactional`` on the module-level ``AsyncClient`` (``core.db.get_db``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from google.api_core import exceptions as gexc
from google.cloud import firestore
from google.cloud.firestore_v1.async_document import AsyncDocumentReference
from google.cloud.firestore_v1.async_transaction import AsyncTransaction
from google.cloud.firestore_v1.base_document import DocumentSnapshot

from topskill import ratelimit
from topskill.auth import CurrentUser
from topskill.billing import money
from topskill.config import get_settings
from topskill.db import Col, get_db, utcnow
from topskill.domain import AgentDoc, MessageWriter, Pricing, RoundCharge, RoundQuote
from topskill.errors import ApiError, Codes

log = logging.getLogger(__name__)

# Paid rounds of many visitors write the same creator wallet; give contended transactions room to retry.
TX_MAX_ATTEMPTS = 25

DEMO_TOPUP_BUCKET = "demo_topup"


# --------------------------------------------------------------------------- references


def wallet_ref(uid: str) -> AsyncDocumentReference:
    return get_db().collection(Col.WALLETS).document(uid)


def ledger_ref(uid: str, request_id: str) -> AsyncDocumentReference:
    return get_db().collection(Col.LEDGER).document(money.ledger_doc_id(uid, request_id))


def usage_ref(agent_id: str, uid: str) -> AsyncDocumentReference:
    return get_db().collection(Col.USAGE).document(f"{agent_id}__{uid}")


def stats_ref(agent_id: str) -> AsyncDocumentReference:
    return get_db().collection(Col.AGENT_STATS).document(agent_id)


def balance_of(snap: DocumentSnapshot | None) -> int | None:
    """Wallet balance in cents, or ``None`` when the wallet does not exist."""
    if snap is None or not snap.exists:
        return None
    return int((snap.to_dict() or {}).get("balance_cents", 0))


async def get_many(
    tx: AsyncTransaction | None, refs: Iterable[AsyncDocumentReference]
) -> dict[str, DocumentSnapshot]:
    """Read several documents in one RPC (inside ``tx`` when given) → ``{path: snapshot}``."""
    unique: dict[str, AsyncDocumentReference] = {}
    for ref in refs:
        unique.setdefault(ref.path, ref)
    out: dict[str, DocumentSnapshot] = {}
    if not unique:
        return out
    async for snap in get_db().get_all(list(unique.values()), transaction=tx):
        out[snap.reference.path] = snap
    return out


def credit_owner(
    tx: AsyncTransaction,
    *,
    owner_uid: str,
    owner_snap: DocumentSnapshot | None,
    share_cents: int,
    now: Any,
) -> int:
    """Add ``share_cents`` to the owner's wallet inside ``tx``; returns the new balance.

    Last line of defence for owners without a wallet (§5.1): the wallet is created in the same transaction
    with ``SIGNUP_GRANT + share`` plus its ``signup`` ledger row.
    """
    current = balance_of(owner_snap)
    if current is None:
        grant = get_settings().signup_grant_cents
        new_balance = grant + share_cents
        tx.create(wallet_ref(owner_uid), money.wallet_doc(owner_uid, new_balance, now))
        tx.create(
            ledger_ref(owner_uid, money.SIGNUP_REQUEST_ID), money.signup_ledger_doc(owner_uid, grant, now)
        )
        return new_balance
    new_balance = current + share_cents
    tx.update(wallet_ref(owner_uid), {"balance_cents": new_balance, "updated_at": now})
    return new_balance


# --------------------------------------------------------------------------- T1 open wallet


async def get_or_open_wallet(uid: str) -> int:
    """Balance in cents; opens the wallet with the signup grant on first use (T1, idempotent)."""
    ref = wallet_ref(uid)
    existing = balance_of(await ref.get())
    if existing is not None:
        return existing

    @firestore.async_transactional
    async def _open(tx: AsyncTransaction) -> int:
        current = balance_of(await ref.get(transaction=tx))
        if current is not None:
            return current
        now = utcnow()
        grant = get_settings().signup_grant_cents
        tx.create(ref, money.wallet_doc(uid, grant, now))
        tx.create(ledger_ref(uid, money.SIGNUP_REQUEST_ID), money.signup_ledger_doc(uid, grant, now))
        return grant

    try:
        return await _open(get_db().transaction(max_attempts=TX_MAX_ATTEMPTS))
    except gexc.AlreadyExists:
        # A concurrent opener won between our read and commit; its wallet is the one that counts.
        current = balance_of(await ref.get())
        if current is None:
            raise
        return current


async def get_balance(uid: str) -> int:
    """Balance for display only (0 for a missing wallet); never used in a gate."""
    return balance_of(await wallet_ref(uid).get()) or 0


# --------------------------------------------------------------------------- round gate (§8.2)


def _offline(agent: AgentDoc) -> ApiError:
    return ApiError(410, Codes.AGENT_OFFLINE, "This agent is temporarily unavailable.", {"slug": agent.slug})


def owner_unavailable(agent: AgentDoc) -> ApiError:
    return ApiError(
        402,
        Codes.INSUFFICIENT_POINTS,
        "This agent is temporarily unavailable.",
        {"reason": "owner", "slug": agent.slug},
    )


def _fmt(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


async def rounds_used(agent_id: str, uid: str) -> int:
    snap = await usage_ref(agent_id, uid).get()
    return int((snap.to_dict() or {}).get("rounds_used", 0)) if snap.exists else 0


async def preflight_round(agent: AgentDoc, payer: CurrentUser) -> RoundQuote:
    """Gate before the SSE stream opens (§8.2). Opens payer and owner wallets; 410 / 402. Advisory."""
    if agent.deleted or agent.status != "active":
        raise _offline(agent)
    payer_balance = await get_or_open_wallet(payer.uid)
    is_owner = payer.uid == agent.owner_uid
    owner_balance = payer_balance if is_owner else await get_or_open_wallet(agent.owner_uid)
    if owner_balance <= 0:
        raise owner_unavailable(agent)
    if is_owner:
        return RoundQuote(
            is_owner=True, round_index=None, charge_cents=0, free=True, balance_cents=payer_balance
        )
    pricing = agent.pricing
    round_index = await rounds_used(agent.id, payer.uid) + 1
    if pricing.mode == "free" or round_index <= pricing.free_rounds:
        return RoundQuote(
            is_owner=False, round_index=round_index, charge_cents=0, free=True, balance_cents=payer_balance
        )
    price = pricing.price_cents
    if payer_balance < price:
        raise ApiError(
            402,
            Codes.VISITOR_INSUFFICIENT_POINTS,
            f"You need {_fmt(price)} credits; you have {_fmt(payer_balance)}.",
            {
                "required_cents": price,
                "balance_cents": payer_balance,
                "price_cents": price,
                "billing_mode": "per_round",
                "slug": agent.slug,
            },
        )
    return RoundQuote(
        is_owner=False, round_index=round_index, charge_cents=price, free=False, balance_cents=payer_balance
    )


def _free_rounds_left(pricing: Pricing, is_owner: bool, used: int) -> int | None:
    if is_owner or pricing.mode == "free":
        return None
    return max(0, pricing.free_rounds - used)


async def billing_status(agent: AgentDoc, user: CurrentUser) -> dict:
    """``BillingStatus`` (§6.3) for the agent page: what the next round costs this account."""
    pricing = agent.pricing
    is_owner = user.uid == agent.owner_uid
    balance = await get_or_open_wallet(user.uid)
    owner_balance = balance if is_owner else await get_or_open_wallet(agent.owner_uid)
    used = 0 if is_owner else await rounds_used(agent.id, user.uid)
    if is_owner or pricing.mode == "free" or used + 1 <= pricing.free_rounds:
        next_charge = 0
    else:
        next_charge = pricing.price_cents
    owner_available = owner_balance > 0 and agent.status == "active" and not agent.deleted
    return {
        "billing_mode": pricing.mode,
        "price_cents": pricing.price_cents if pricing.mode == "per_round" else 0,
        "free_rounds": pricing.free_rounds,
        "is_owner": is_owner,
        "used_rounds": used,
        "free_rounds_left": _free_rounds_left(pricing, is_owner, used),
        "next_round_charge_cents": next_charge,
        "balance_cents": balance,
        "can_afford_next_round": owner_available and balance >= next_charge,
        "owner_available": owner_available,
    }


# --------------------------------------------------------------------------- T2 settle round (§8.5)


def _live_pricing(agent_snap: DocumentSnapshot | None, fallback: AgentDoc) -> tuple[Pricing, str, str]:
    """(pricing, title, slug) read live from the agent doc inside T2; falls back to the passed agent."""
    data = agent_snap.to_dict() if agent_snap is not None and agent_snap.exists else None
    if not data:
        return fallback.pricing, fallback.title, fallback.slug
    try:
        pricing = Pricing.model_validate(data.get("pricing") or {})
    except ValueError:
        log.warning("settle_round_bad_pricing agent_id=%s", fallback.id)
        pricing = fallback.pricing
    return pricing, str(data.get("title") or fallback.title), str(data.get("slug") or fallback.slug)


async def settle_round(
    *,
    agent: AgentDoc,
    payer: CurrentUser,
    conversation_id: str,
    request_id: str,
    succeeded: bool,
    model: str | None,
    write_messages: MessageWriter,
) -> RoundCharge:
    """Transaction T2: persist the round's messages (chat writer) and bill it, atomically.

    Idempotent per ``request_id``: if ``m_{request_id}`` already exists nothing is written and the stored
    charge is returned with ``outcome="duplicate"``. Raises if the transaction fails (nothing persisted).
    """
    db = get_db()
    conv_ref = db.collection(Col.CONVERSATIONS).document(conversation_id)
    msg_ref = conv_ref.collection(Col.MESSAGES).document(f"m_{request_id}")
    agent_ref = db.collection(Col.AGENTS).document(agent.id)
    owner_uid = agent.owner_uid
    is_owner = payer.uid == owner_uid
    payer_wallet = wallet_ref(payer.uid)
    owner_wallet = wallet_ref(owner_uid)
    usage = usage_ref(agent.id, payer.uid)

    @firestore.async_transactional
    async def _settle(tx: AsyncTransaction) -> RoundCharge:
        snaps = await get_many(tx, [msg_ref, conv_ref, agent_ref, payer_wallet, owner_wallet, usage])
        msg_snap = snaps.get(msg_ref.path)
        conv_snap = snaps.get(conv_ref.path)
        pricing, title, slug = _live_pricing(snaps.get(agent_ref.path), agent)
        payer_balance = balance_of(snaps.get(payer_wallet.path))
        owner_snap = snaps.get(owner_wallet.path)
        usage_snap = snaps.get(usage.path)
        usage_data = (usage_snap.to_dict() or {}) if usage_snap is not None and usage_snap.exists else {}
        used = int(usage_data.get("rounds_used", 0))
        paid = int(usage_data.get("paid_rounds", 0))

        if msg_snap is not None and msg_snap.exists:
            stored = (msg_snap.to_dict() or {}).get("charge") or {}
            charged = int(stored.get("charged_cents") or 0)
            return RoundCharge(
                request_id=request_id,
                outcome="duplicate",
                charged_cents=charged,
                creator_share_cents=0 if is_owner else money.creator_share(charged),
                round_index=stored.get("round_index"),
                free_rounds_left=_free_rounds_left(pricing, is_owner, used),
                balance_cents=payer_balance or 0,
            )

        conversation_exists = bool(conv_snap is not None and conv_snap.exists)
        balance = payer_balance or 0
        round_index: int | None = None
        charged = 0
        if not succeeded:
            outcome = "not_counted"
        elif is_owner:
            outcome = "free"
        else:
            # Live pricing, but never above what the turn started with (``agent`` is the doc read before
            # preflight): a price raised mid-turn must not charge the visitor more than the quote.
            start = agent.pricing
            round_index = used + 1
            if (
                pricing.mode == "free"
                or start.mode == "free"
                or round_index <= max(pricing.free_rounds, start.free_rounds)
            ):
                outcome = "free"
            else:
                charged = min(pricing.price_cents, start.price_cents, max(balance, 0))
                outcome = "charged" if charged > 0 else "free"
        share = money.creator_share(charged)
        counted = succeeded and not is_owner
        used_after = used + 1 if counted else used
        charge = RoundCharge(
            request_id=request_id,
            outcome=outcome,  # type: ignore[arg-type]
            charged_cents=charged,
            creator_share_cents=share,
            round_index=round_index,
            free_rounds_left=_free_rounds_left(pricing, is_owner, used_after),
            balance_cents=balance - charged,
        )

        # ---- writes (only now)
        now = utcnow()
        write_messages(tx, charge, conversation_exists)
        if counted:
            tx.set(
                usage,
                {
                    "agent_id": agent.id,
                    "uid": payer.uid,
                    "rounds_used": used_after,
                    "paid_rounds": paid + (1 if charged else 0),
                    "updated_at": now,
                },
            )
        if charged:
            new_balance = balance - charged
            tx.update(payer_wallet, {"balance_cents": new_balance, "updated_at": now})
            tx.create(
                ledger_ref(payer.uid, request_id),
                money.ledger_doc(
                    uid=payer.uid,
                    request_id=request_id,
                    category=money.VISITOR_PAYMENT,
                    amount_cents=-charged,
                    balance_after_cents=new_balance,
                    created_at=now,
                    memo=money.round_memo(title, round_index),
                    unit_price_cents=pricing.price_cents,
                    billing_mode="per_round",
                    counterparty_uid=owner_uid,
                    agent_id=agent.id,
                    agent_slug=slug,
                    agent_title=title,
                    conversation_id=conversation_id,
                    round_index=round_index,
                    model=model,
                ),
            )
            if share:
                owner_balance = credit_owner(
                    tx, owner_uid=owner_uid, owner_snap=owner_snap, share_cents=share, now=now
                )
                income_id = money.income_request_id(request_id)
                tx.create(
                    ledger_ref(owner_uid, income_id),
                    money.ledger_doc(
                        uid=owner_uid,
                        request_id=income_id,
                        category=money.CREATOR_INCOME,
                        amount_cents=share,
                        balance_after_cents=owner_balance,
                        created_at=now,
                        memo=money.income_memo(title, round_index, payer.display_name),
                        unit_price_cents=pricing.price_cents,
                        billing_mode="per_round",
                        counterparty_uid=payer.uid,
                        agent_id=agent.id,
                        agent_slug=slug,
                        agent_title=title,
                        conversation_id=conversation_id,
                        round_index=round_index,
                        model=model,
                    ),
                )
        stats: dict[str, Any] = {
            "agent_id": agent.id,
            "owner_uid": owner_uid,
            "rounds": firestore.Increment(1 if succeeded else 0),
            "paid_rounds": firestore.Increment(1 if charged else 0),
            "conversations": firestore.Increment(0 if conversation_exists else 1),
            "visitor_gross_cents": firestore.Increment(charged),
            "creator_earned_cents": firestore.Increment(share),
            "last_activity_at": now,
        }
        tx.set(stats_ref(agent.id), stats, merge=True)
        return charge

    charge = await _settle(db.transaction(max_attempts=TX_MAX_ATTEMPTS))
    log.info(
        "round_settled agent_id=%s request_id=%s outcome=%s charged_cents=%d share_cents=%d round_index=%s",
        agent.id,
        request_id,
        charge.outcome,
        charge.charged_cents,
        charge.creator_share_cents,
        charge.round_index,
    )
    return charge


# --------------------------------------------------------------------------- T4 demo top-up


async def demo_topup(user: CurrentUser) -> tuple[int, str, dict[str, Any]]:
    """Transaction T4: add ``DEMO_TOPUP_CENTS``, at most ``DEMO_TOPUP_DAILY_LIMIT`` times per UTC day.

    Returns ``(new balance, ledger doc id, ledger doc)``; 429 ``rate_limited`` over the daily limit.
    """
    settings = get_settings()
    await get_or_open_wallet(user.uid)
    now = utcnow()
    day_key, day_end = ratelimit.window_bounds(now, "day")
    limit_ref = (
        get_db().collection(Col.RATE_LIMITS).document(ratelimit.doc_id(DEMO_TOPUP_BUCKET, user.uid, day_key))
    )
    ref = wallet_ref(user.uid)
    amount = settings.demo_topup_cents
    limit = settings.demo_topup_daily_limit

    @firestore.async_transactional
    async def _topup(tx: AsyncTransaction) -> tuple[int, str, dict[str, Any]]:
        snaps = await get_many(tx, [ref, limit_ref])
        limit_snap = snaps.get(limit_ref.path)
        count = int((limit_snap.to_dict() or {}).get("count", 0)) if limit_snap and limit_snap.exists else 0
        if count >= limit:
            retry_after = max(1, int((day_end - utcnow()).total_seconds()))
            raise ApiError(
                429,
                Codes.RATE_LIMITED,
                "You have used all demo top-ups for today. Please try again tomorrow.",
                {"retry_after_s": retry_after},
            )
        current = balance_of(snaps.get(ref.path))
        n = count + 1
        request_id = money.topup_request_id(day_key, n)
        ts = utcnow()
        if current is None:  # wallet vanished between open and now: recreate with the grant
            grant = settings.signup_grant_cents
            new_balance = grant + amount
            tx.create(ref, money.wallet_doc(user.uid, new_balance, ts))
            tx.create(
                ledger_ref(user.uid, money.SIGNUP_REQUEST_ID), money.signup_ledger_doc(user.uid, grant, ts)
            )
        else:
            new_balance = current + amount
            tx.update(ref, {"balance_cents": new_balance, "updated_at": ts})
        entry = money.ledger_doc(
            uid=user.uid,
            request_id=request_id,
            category=money.DEMO_TOPUP,
            amount_cents=amount,
            balance_after_cents=new_balance,
            created_at=ts,
            memo=money.DEMO_TOPUP_MEMO,
        )
        tx.create(ledger_ref(user.uid, request_id), entry)
        tx.set(limit_ref, {"count": n, "expires_at": day_end})
        return new_balance, money.ledger_doc_id(user.uid, request_id), entry

    return await _topup(get_db().transaction(max_attempts=TX_MAX_ATTEMPTS))
