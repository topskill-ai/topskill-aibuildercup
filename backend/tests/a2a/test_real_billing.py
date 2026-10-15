"""Paid A2A calls settled by the real ``topskill.billing`` package (T3 with ledger rows) instead of the
test double. It proves the a2a ↔ billing contract end to end: charged once on success, nothing charged and the reservation released on failure."""

from __future__ import annotations

import importlib
import importlib.util
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from types import ModuleType

import pytest
from a2a.client import A2AClientError

from tests.a2a.conftest import issue_mandate
from tests.a2a.fakes import RuntimeFactory
from tests.a2a.test_e2e import send
from topskill.a2a import _deps, call_id_for, executor
from topskill.a2a.assistant import a2a_error_code, extract_result
from topskill.db import Col, get_db
from topskill.testing.factories import make_agent, make_user, open_wallet, unique

pytestmark = pytest.mark.emulator


@dataclass(frozen=True)
class _RealDeps:
    billing: ModuleType


@pytest.fixture
def real_billing(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[ModuleType, RuntimeFactory]]:
    billing = importlib.import_module("topskill.billing")
    runtime = RuntimeFactory()
    monkeypatch.setattr(_deps, "get_deps", lambda: _RealDeps(billing=billing))
    monkeypatch.setattr(_deps, "build_runtime_agent", runtime)
    executor.clear_stacks()
    yield billing, runtime
    executor.clear_stacks()


async def _doc(col: str, doc_id: str) -> dict:
    return (await get_db().collection(col).document(doc_id).get()).to_dict() or {}


async def _balance(uid: str) -> int:
    return int((await _doc(Col.WALLETS, uid)).get("balance_cents", 0))


async def _setup(client, price: int):
    owner = await make_user("creator")
    agent = await make_agent(owner, pricing={"a2a_price_cents": price}, title="Visa Advisor")
    payer = unique("u_")
    await make_user("visitor", uid=payer)
    await open_wallet(payer, 10_000)
    mandate, token = await issue_mandate(client, payer, max_amount_cents=500, budget_cents=2000)
    return owner, agent, payer, mandate, token


async def test_real_billing_charges_once_and_writes_ledger(app, client, real_billing) -> None:
    billing, _ = real_billing
    owner, agent, payer, mandate, token = await _setup(client, price=200)
    owner_before = await _balance(owner.uid)
    message_id = uuid.uuid4().hex

    out = await send(app, agent.slug, "Plan a Brazil work visa", token, message_id=message_id)
    assert extract_result(out)[0] == "TASK_STATE_COMPLETED"

    call_id = call_id_for(mandate["id"], message_id)
    call = await _doc(Col.A2A_CALLS, call_id)
    share = billing.creator_share(200)
    assert (call["status"], call["charged_cents"], call["creator_share_cents"]) == ("completed", 200, share)
    m = await _doc(Col.MANDATES, mandate["id"])
    assert (m["spent_cents"], m["reserved_cents"], m["calls"]) == (200, 0, 1)
    assert await _balance(payer) == 9_800
    assert await _balance(owner.uid) == owner_before + share
    debit = await _doc(Col.LEDGER, f"{payer}__a2a_{call_id}")
    credit = await _doc(Col.LEDGER, f"{owner.uid}__a2a_income_{call_id}")
    assert debit["category"] == "a2a_payment" and abs(debit["amount_cents"]) == 200
    assert credit["category"] == "a2a_income" and credit["amount_cents"] == share

    with pytest.raises(A2AClientError) as exc:
        await send(app, agent.slug, "again", token, message_id=message_id)
    assert a2a_error_code(exc.value) == ("mandate_replay", 402)
    assert await _balance(payer) == 9_800


async def test_real_billing_failed_run_releases_reservation(app, client, real_billing) -> None:
    _, runtime = real_billing
    owner, agent, payer, mandate, token = await _setup(client, price=300)
    runtime.scripts[agent.slug] = [RuntimeError("model exploded")]
    owner_before = await _balance(owner.uid)
    message_id = uuid.uuid4().hex

    out = await send(app, agent.slug, "Plan a Brazil work visa", token, message_id=message_id)
    assert extract_result(out)[0] == "TASK_STATE_FAILED"

    call = await _doc(Col.A2A_CALLS, call_id_for(mandate["id"], message_id))
    assert call["status"] == "failed" and call["charged_cents"] == 0
    m = await _doc(Col.MANDATES, mandate["id"])
    assert (m["spent_cents"], m["reserved_cents"], m["calls"]) == (0, 0, 0)
    assert await _balance(payer) == 10_000
    assert await _balance(owner.uid) == owner_before


async def test_real_billing_price_over_cap_is_rejected(app, client, real_billing) -> None:
    _, agent, payer, mandate, token = await _setup(client, price=600)
    with pytest.raises(A2AClientError) as exc:
        await send(app, agent.slug, "Review my contract", token)
    assert a2a_error_code(exc.value) == ("mandate_exceeds_max", 402)
    m = await _doc(Col.MANDATES, mandate["id"])
    assert (m["spent_cents"], m["reserved_cents"]) == (0, 0)
    assert await _balance(payer) == 10_000
