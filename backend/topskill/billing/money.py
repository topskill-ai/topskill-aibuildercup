"""Money rules and ledger document shapes (SPEC §5.1, §8.1, §8.3, §8.4).

All amounts are integer cents of simulated credits. Nothing here does I/O.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from topskill.config import get_settings

LedgerCategory = Literal[
    "signup_grant", "demo_topup", "visitor_payment", "creator_income", "a2a_payment", "a2a_income", "adjust"
]
LedgerKind = Literal["CREDIT", "DEBIT", "ADJUST"]

SIGNUP_GRANT: LedgerCategory = "signup_grant"
DEMO_TOPUP: LedgerCategory = "demo_topup"
VISITOR_PAYMENT: LedgerCategory = "visitor_payment"
CREATOR_INCOME: LedgerCategory = "creator_income"
A2A_PAYMENT: LedgerCategory = "a2a_payment"
A2A_INCOME: LedgerCategory = "a2a_income"
ADJUST: LedgerCategory = "adjust"

CATEGORIES: frozenset[str] = frozenset(
    {SIGNUP_GRANT, DEMO_TOPUP, VISITOR_PAYMENT, CREATOR_INCOME, A2A_PAYMENT, A2A_INCOME, ADJUST}
)
INCOME_CATEGORIES: tuple[str, ...] = (CREATOR_INCOME, A2A_INCOME)

# Category → kind (direction only; always filter and aggregate by category, never by kind).
KIND_OF: dict[str, LedgerKind] = {
    SIGNUP_GRANT: "CREDIT",
    DEMO_TOPUP: "CREDIT",
    VISITOR_PAYMENT: "DEBIT",
    CREATOR_INCOME: "CREDIT",
    A2A_PAYMENT: "DEBIT",
    A2A_INCOME: "CREDIT",
    ADJUST: "ADJUST",
}

SIGNUP_REQUEST_ID = "signup"
SIGNUP_MEMO = "Welcome credits"
DEMO_TOPUP_MEMO = "Demo credits"


def creator_share(amount_cents: int) -> int:
    """The creator's share of a paid amount: floor(amount × BP / 10000), so share ≤ ratio, never negative."""
    if amount_cents <= 0:
        return 0
    return amount_cents * get_settings().creator_revenue_share_bp // 10000


# ---- idempotency keys (§8.4)


def ledger_doc_id(uid: str, request_id: str) -> str:
    return f"{uid}__{request_id}"


def income_request_id(request_id: str) -> str:
    """Creator credit for a round: ``income_turn_<hex>``."""
    return f"income_{request_id}"


def a2a_payment_request_id(call_id: str) -> str:
    return f"a2a_{call_id}"


def a2a_income_request_id(call_id: str) -> str:
    return f"a2a_income_{call_id}"


def topup_request_id(day_key: str, n: int) -> str:
    """``topup_<YYYYMMDD>_<n>`` (n = 1-based count of the day's top-ups)."""
    return f"topup_{day_key}_{n}"


def a2a_call_id(mandate_id: str, message_id: str) -> str:
    """``sha256(f"{mandate_id}:{message_id}")[:32]`` (SPEC §5.1, §8.6 step 7)."""
    return hashlib.sha256(f"{mandate_id}:{message_id}".encode()).hexdigest()[:32]


# ---- memos (§8.3)


def round_memo(agent_title: str, round_index: int | None) -> str:
    return f"{agent_title} · round {round_index}" if round_index is not None else agent_title


def income_memo(agent_title: str, round_index: int | None, payer_name: str) -> str:
    return f"{round_memo(agent_title, round_index)} by {payer_name}"


def a2a_memo(agent_title: str) -> str:
    return f"{agent_title} · A2A call"


# ---- documents


def wallet_doc(uid: str, balance_cents: int, now: datetime) -> dict[str, Any]:
    return {"uid": uid, "balance_cents": balance_cents, "opened_at": now, "updated_at": now}


def ledger_doc(
    *,
    uid: str,
    request_id: str,
    category: LedgerCategory,
    amount_cents: int,
    balance_after_cents: int,
    created_at: datetime,
    memo: str,
    unit_price_cents: int | None = None,
    billing_mode: Literal["per_round", "a2a"] | None = None,
    counterparty_uid: str | None = None,
    agent_id: str | None = None,
    agent_slug: str | None = None,
    agent_title: str | None = None,
    conversation_id: str | None = None,
    mandate_id: str | None = None,
    a2a_call_id: str | None = None,
    round_index: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """One append-only ledger row, exactly the §5.1 shape."""
    return {
        "uid": uid,
        "request_id": request_id,
        "kind": KIND_OF[category],
        "category": category,
        "amount_cents": amount_cents,
        "balance_after_cents": balance_after_cents,
        "unit_price_cents": unit_price_cents,
        "billing_mode": billing_mode,
        "counterparty_uid": counterparty_uid,
        "agent_id": agent_id,
        "agent_slug": agent_slug,
        "agent_title": agent_title,
        "conversation_id": conversation_id,
        "mandate_id": mandate_id,
        "a2a_call_id": a2a_call_id,
        "round_index": round_index,
        "model": model,
        "memo": memo,
        "created_at": created_at,
    }


def signup_ledger_doc(uid: str, grant_cents: int, now: datetime) -> dict[str, Any]:
    return ledger_doc(
        uid=uid,
        request_id=SIGNUP_REQUEST_ID,
        category=SIGNUP_GRANT,
        amount_cents=grant_cents,
        balance_after_cents=grant_cents,
        created_at=now,
        memo=SIGNUP_MEMO,
    )
