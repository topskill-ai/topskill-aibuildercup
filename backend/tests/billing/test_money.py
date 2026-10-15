from __future__ import annotations

import hashlib

import pytest

from tests.core.helpers import settings_env
from topskill import billing
from topskill.billing import money

pytestmark = pytest.mark.unit


def test_creator_share_floors_and_never_exceeds_ratio() -> None:
    assert billing.creator_share(100) == 80
    assert billing.creator_share(1) == 0  # 0.8 cents floors to 0
    assert billing.creator_share(199) == 159
    assert billing.creator_share(0) == 0
    assert billing.creator_share(-5) == 0
    for amount in range(0, 1000, 7):
        assert billing.creator_share(amount) * 10000 <= amount * 8000


def test_creator_share_follows_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    with settings_env(monkeypatch, creator_revenue_share_bp="7000"):
        assert billing.creator_share(100) == 70
        assert billing.creator_share(3) == 2
    assert billing.creator_share(100) == 80


def test_ids_and_memos() -> None:
    assert money.ledger_doc_id("u1", "turn_ab") == "u1__turn_ab"
    assert money.income_request_id("turn_ab") == "income_turn_ab"
    assert money.a2a_payment_request_id("c1") == "a2a_c1"
    assert money.a2a_income_request_id("c1") == "a2a_income_c1"
    assert money.topup_request_id("20261016", 2) == "topup_20261016_2"
    assert money.a2a_call_id("mdt_1", "msg") == hashlib.sha256(b"mdt_1:msg").hexdigest()[:32]
    assert money.round_memo("Visa Advisor", 3) == "Visa Advisor · round 3"
    assert money.income_memo("Visa Advisor", 3, "Ana") == "Visa Advisor · round 3 by Ana"
    assert money.a2a_memo("Visa Advisor") == "Visa Advisor · A2A call"


def test_ledger_doc_shape() -> None:
    from topskill.db import utcnow

    row = money.ledger_doc(
        uid="u",
        request_id="turn_x",
        category=money.VISITOR_PAYMENT,
        amount_cents=-100,
        balance_after_cents=9900,
        created_at=utcnow(),
        memo="m",
    )
    assert row["kind"] == "DEBIT" and row["category"] == "visitor_payment"
    assert set(row) == {
        "uid",
        "request_id",
        "kind",
        "category",
        "amount_cents",
        "balance_after_cents",
        "unit_price_cents",
        "billing_mode",
        "counterparty_uid",
        "agent_id",
        "agent_slug",
        "agent_title",
        "conversation_id",
        "mandate_id",
        "a2a_call_id",
        "round_index",
        "model",
        "memo",
        "created_at",
    }
    assert {money.KIND_OF[c] for c in money.INCOME_CATEGORIES} == {"CREDIT"}


def test_package_exports_contract() -> None:
    for name in (
        "get_or_open_wallet",
        "get_balance",
        "creator_share",
        "preflight_round",
        "billing_status",
        "settle_round",
        "preflight_a2a",
        "settle_a2a_call",
        "RoundQuote",
        "RoundCharge",
        "MessageWriter",
        "A2ACharge",
    ):
        assert hasattr(billing, name), name
    assert billing.is_stub is False
