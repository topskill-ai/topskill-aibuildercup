"""Billing: wallets, the append-only ledger, the round gate and settlement (SPEC §8, §14.2).

Other packages use only the names re-exported here (``chat`` and ``a2a`` reach them through
``chat.deps.get_deps()``). ``is_stub`` is False so callers can tell the real module from
``topskill.testing.stubs.stub_billing``.
"""

from topskill.billing.a2a_billing import preflight_a2a, settle_a2a_call
from topskill.billing.money import creator_share
from topskill.billing.service import (
    billing_status,
    get_balance,
    get_or_open_wallet,
    preflight_round,
    settle_round,
)
from topskill.domain import A2ACharge, MessageWriter, RoundCharge, RoundQuote

is_stub = False

__all__ = [
    "A2ACharge",
    "MessageWriter",
    "RoundCharge",
    "RoundQuote",
    "billing_status",
    "creator_share",
    "get_balance",
    "get_or_open_wallet",
    "is_stub",
    "preflight_a2a",
    "preflight_round",
    "settle_a2a_call",
    "settle_round",
]
