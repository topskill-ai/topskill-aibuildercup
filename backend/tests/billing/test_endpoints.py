"""Wallet & billing HTTP endpoints (§6.4) and wallet transactions T1/T4 on the emulator."""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta

import httpx
import pytest

from tests.billing.helpers import balance, ledger, make_mandate, settle
from tests.conftest import dev_headers
from tests.core.helpers import settings_env
from topskill import billing
from topskill.billing import money
from topskill.billing.a2a_billing import reserve_a2a_call
from topskill.db import Col, get_db, new_id, utcnow
from topskill.testing.factories import make_agent, make_user, open_wallet, unique

pytestmark = pytest.mark.emulator

LEDGER_KEYS = {
    "id",
    "request_id",
    "kind",
    "category",
    "amount_cents",
    "balance_after_cents",
    "unit_price_cents",
    "billing_mode",
    "counterparty_uid",
    "counterparty_name",
    "agent_id",
    "agent_slug",
    "agent_title",
    "conversation_id",
    "mandate_id",
    "a2a_call_id",
    "round_index",
    "memo",
    "created_at",
}
STATS_KEYS = {
    "rounds",
    "paid_rounds",
    "conversations",
    "visitor_gross_cents",
    "creator_earned_cents",
    "a2a_calls",
    "a2a_gross_cents",
    "records",
    "last_activity_at",
}


def h(user) -> dict[str, str]:
    return dev_headers(user.uid)


# --------------------------------------------------------------------------- wallet (T1)


async def test_wallet_opens_once_with_signup_grant(client: httpx.AsyncClient) -> None:
    uid = unique("u_")
    resp = await client.get("/api/wallet", headers=dev_headers(uid))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"balance_cents": 10000, "currency": "credits"}
    assert (await client.get("/api/wallet", headers=dev_headers(uid))).json()["balance_cents"] == 10000
    rows = await ledger(uid)
    assert list(rows) == [f"{uid}__signup"]
    assert (
        rows[f"{uid}__signup"]["category"] == "signup_grant"
        and rows[f"{uid}__signup"]["amount_cents"] == 10000
    )
    assert (await client.get("/api/wallet")).status_code == 401


async def test_concurrent_wallet_open_is_idempotent() -> None:
    uid = unique("u_")
    assert await billing.get_balance(uid) == 0  # display only; does not open
    assert await balance(uid) is None
    results = await asyncio.gather(*(billing.get_or_open_wallet(uid) for _ in range(6)))
    assert results == [10000] * 6
    assert list(await ledger(uid)) == [f"{uid}__signup"]
    assert await billing.get_balance(uid) == 10000


# --------------------------------------------------------------------------- demo top-up (T4)


async def test_demo_topup_and_daily_limit(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    user = await make_user()
    day = utcnow().strftime("%Y%m%d")
    with settings_env(monkeypatch, demo_topup_daily_limit="2", demo_topup_cents="5000"):
        first = await client.post("/api/wallet/demo-topup", json={}, headers=h(user))
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["wallet"] == {"balance_cents": 15000, "currency": "credits"}
        entry = body["entry"]
        assert set(entry) == LEDGER_KEYS
        assert entry["id"] == f"{user.uid}__topup_{day}_1" and entry["request_id"] == f"topup_{day}_1"
        assert entry["category"] == "demo_topup" and entry["kind"] == "CREDIT"
        assert entry["amount_cents"] == 5000 and entry["balance_after_cents"] == 15000
        assert entry["created_at"].endswith("Z") and entry["counterparty_name"] is None
        second = await client.post("/api/wallet/demo-topup", json={}, headers=h(user))
        assert second.json()["entry"]["request_id"] == f"topup_{day}_2"
        third = await client.post("/api/wallet/demo-topup", json={}, headers=h(user))
        assert third.status_code == 429 and third.json()["code"] == "rate_limited"
        assert third.json()["detail"]["retry_after_s"] >= 1
    assert await balance(user.uid) == 20000
    rl = (
        await get_db().collection(Col.RATE_LIMITS).document(f"demo_topup__{user.uid}__{day}").get()
    ).to_dict()
    assert rl["count"] == 2 and rl["expires_at"] > utcnow()


async def test_concurrent_topups_respect_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    user = await make_user()
    from topskill.billing import service
    from topskill.errors import ApiError

    with settings_env(monkeypatch, demo_topup_daily_limit="2"):
        results = await asyncio.gather(*(service.demo_topup(user) for _ in range(3)), return_exceptions=True)
    ok = [r for r in results if not isinstance(r, BaseException)]
    assert len(ok) == 2
    assert all(isinstance(r, ApiError) and r.code == "rate_limited" for r in results if r not in ok)
    assert await balance(user.uid) == 10000 + 2 * 5000
    assert sorted(r[1].rsplit("_", 1)[1] for r in ok) == ["1", "2"]


# --------------------------------------------------------------------------- ledger


async def _paid_world():
    owner = await make_user("creator", display_name="Olivia Owner")
    visitor = await make_user(display_name="Ana Silva")
    agent = await make_agent(owner, title="Visa Advisor", pricing={"free_rounds": 0})
    other = await make_agent(
        owner, open_owner_wallet=False, title="Resume Reviewer", pricing={"free_rounds": 0}
    )
    await billing.get_or_open_wallet(visitor.uid)
    r1, _ = await settle(billing, agent, visitor)
    r2, _ = await settle(billing, agent, visitor)
    r3, _ = await settle(billing, other, visitor)
    return owner, visitor, agent, other, [r1, r2, r3]


async def test_ledger_filters_pagination_and_names(client: httpx.AsyncClient) -> None:
    owner, visitor, agent, other, _ = await _paid_world()
    await client.post("/api/wallet/demo-topup", json={}, headers=h(visitor))

    resp = await client.get("/api/wallet/ledger", headers=h(visitor))
    assert resp.status_code == 200, resp.text
    page = resp.json()
    assert page["next_cursor"] is None
    cats = [e["category"] for e in page["items"]]
    assert cats == ["demo_topup", "visitor_payment", "visitor_payment", "visitor_payment", "signup_grant"]
    assert all(set(e) == LEDGER_KEYS for e in page["items"])
    pay = page["items"][1]
    assert pay["counterparty_uid"] == owner.uid and pay["counterparty_name"] == "Olivia Owner"
    assert pay["agent_title"] == "Resume Reviewer" and pay["amount_cents"] == -100

    def ids(resp: httpx.Response) -> list[str]:
        return [e["id"] for e in resp.json()["items"]]

    by_cat = await client.get(
        "/api/wallet/ledger", params={"category": "visitor_payment"}, headers=h(visitor)
    )
    assert len(ids(by_cat)) == 3
    by_agent = await client.get("/api/wallet/ledger", params={"agent_id": agent.id}, headers=h(visitor))
    assert len(ids(by_agent)) == 2
    both = await client.get(
        "/api/wallet/ledger", params={"category": "visitor_payment", "agent_id": other.id}, headers=h(visitor)
    )
    assert len(ids(both)) == 1
    none = await client.get(
        "/api/wallet/ledger", params={"category": "creator_income", "agent_id": agent.id}, headers=h(visitor)
    )
    assert ids(none) == []

    # owner sees income with the payer's name
    income = await client.get("/api/wallet/ledger", params={"category": "creator_income"}, headers=h(owner))
    items = income.json()["items"]
    assert len(items) == 3 and all(e["counterparty_name"] == "Ana Silva" for e in items)
    assert {e["memo"] for e in items} == {
        "Visa Advisor · round 1 by Ana Silva",
        "Visa Advisor · round 2 by Ana Silva",
        "Resume Reviewer · round 1 by Ana Silva",
    }

    # cursor walk with limit=2 → 5 rows in 3 pages, same order
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        r = await client.get("/api/wallet/ledger", params=params, headers=h(visitor))
        seen += ids(r)
        cursor = r.json()["next_cursor"]
        pages += 1
        if cursor is None:
            break
    assert pages == 3 and seen == ids(resp)

    bad = await client.get("/api/wallet/ledger", params={"category": "bogus"}, headers=h(visitor))
    assert bad.status_code == 400 and bad.json()["code"] == "validation_error"
    bad_cursor = await client.get("/api/wallet/ledger", params={"cursor": "!!"}, headers=h(visitor))
    assert bad_cursor.status_code == 400
    assert (await client.get("/api/wallet/ledger")).status_code == 401


# --------------------------------------------------------------------------- dashboard


async def test_dashboard(client: httpx.AsyncClient) -> None:
    owner, visitor, agent, other, _rounds = await _paid_world()
    hidden = await make_agent(
        owner, open_owner_wallet=False, title="Deleted One", deleted=True, status="paused", listed=False
    )
    mandate = await make_mandate(visitor)
    call_id = await reserve_a2a_call(mandate, agent, "m1", "q", 200)
    await billing.settle_a2a_call(call_id=call_id, succeeded=True, model=None, answer_preview="ok")
    # older income rows: one from 3 days ago (in 7d, not today), one from 10 days ago (total only)
    db = get_db()
    now = utcnow()
    for days, rid in ((3, "adj_recent"), (10, "adj_old")):
        await (
            db.collection(Col.LEDGER)
            .document(f"{owner.uid}__{rid}")
            .set(
                money.ledger_doc(
                    uid=owner.uid,
                    request_id=rid,
                    category=money.CREATOR_INCOME,
                    amount_cents=1000,
                    balance_after_cents=0,
                    created_at=now - timedelta(days=days),
                    memo="older",
                    agent_id=agent.id,
                )
            )
        )

    resp = await client.get("/api/dashboard", headers=h(owner))
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["balance_cents"] == 10000 + 3 * 80 + 160
    assert d["visitor_income_cents"] == 3 * 80 + 2000
    assert d["a2a_income_cents"] == 160
    assert d["earned_total_cents"] == 3 * 80 + 160 + 2000
    assert d["earned_today_cents"] == 3 * 80 + 160
    assert d["earned_7d_cents"] == 3 * 80 + 160 + 1000
    assert d["paid_rounds"] == 3 and d["a2a_calls"] == 1
    assert [a["id"] for a in d["agents"]] == [other.id, agent.id]
    assert hidden.id not in {a["id"] for a in d["agents"]}
    summary = next(a for a in d["agents"] if a["id"] == agent.id)
    assert set(summary) == {
        "id",
        "slug",
        "title",
        "tagline",
        "avatar_url",
        "accent_color",
        "status",
        "listed",
        "pricing",
        "active_version",
        "config_dirty",
        "stats",
        "updated_at",
    }
    assert set(summary["stats"]) == STATS_KEYS
    assert summary["stats"]["paid_rounds"] == 2 and summary["stats"]["a2a_calls"] == 1
    assert summary["stats"]["creator_earned_cents"] == 2 * 80 + 160
    assert summary["pricing"]["a2a_price_cents"] == 200 and summary["avatar_url"] is None
    recent = d["recent_income"]
    assert [e["category"] for e in recent[:4]] == [
        "a2a_income",
        "creator_income",
        "creator_income",
        "creator_income",
    ]
    assert recent[0]["counterparty_name"] == "Ana Silva" and recent[0]["memo"] == "Visa Advisor · A2A call"
    assert len(recent) == 6 and recent[-1]["request_id"] == "adj_old"

    visitor_resp = await client.get("/api/dashboard", headers=h(visitor))
    assert visitor_resp.status_code == 403 and visitor_resp.json()["code"] == "creator_required"


async def test_dashboard_for_new_creator_is_all_zero(client: httpx.AsyncClient) -> None:
    creator = await make_user("creator")
    d = (await client.get("/api/dashboard", headers=h(creator))).json()
    assert d["balance_cents"] == 10000 and d["earned_total_cents"] == 0 and d["agents"] == []
    assert d["recent_income"] == [] and d["paid_rounds"] == 0 and d["a2a_calls"] == 0


# --------------------------------------------------------------------------- earnings


async def test_agent_earnings(client: httpx.AsyncClient) -> None:
    owner, _visitor, agent, _other, rounds = await _paid_world()
    resp = await client.get(f"/api/agents/{agent.id}/earnings", headers=h(owner))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"stats", "items", "next_cursor"}
    assert body["stats"]["paid_rounds"] == 2 and body["stats"]["visitor_gross_cents"] == 200
    assert body["stats"]["creator_earned_cents"] == 160 and body["stats"]["last_activity_at"].endswith("Z")
    assert [e["request_id"] for e in body["items"]] == [
        f"income_{rounds[1].request_id}",
        f"income_{rounds[0].request_id}",
    ]
    first = await client.get(f"/api/agents/{agent.id}/earnings", params={"limit": 1}, headers=h(owner))
    assert len(first.json()["items"]) == 1 and first.json()["next_cursor"]
    nxt = await client.get(
        f"/api/agents/{agent.id}/earnings",
        params={"limit": 1, "cursor": first.json()["next_cursor"]},
        headers=h(owner),
    )
    assert nxt.json()["items"][0]["request_id"] == f"income_{rounds[0].request_id}"
    assert nxt.json()["next_cursor"] is None

    stranger = await make_user("creator")
    for user in (stranger, _visitor):
        r = await client.get(f"/api/agents/{agent.id}/earnings", headers=h(user))
        assert r.status_code == 404 and r.json()["code"] == "agent_not_found"
    missing = await client.get(f"/api/agents/{new_id('agt')}/earnings", headers=h(owner))
    assert missing.status_code == 404


# --------------------------------------------------------------------------- billing status


async def test_billing_status(client: httpx.AsyncClient) -> None:
    owner = await make_user("creator")
    visitor = await make_user()
    agent = await make_agent(owner, pricing={"free_rounds": 2, "price_cents": 150})
    url = f"/api/public/agents/{agent.slug}/billing"

    resp = await client.get(url, headers=h(visitor))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "billing_mode": "per_round",
        "price_cents": 150,
        "free_rounds": 2,
        "is_owner": False,
        "used_rounds": 0,
        "free_rounds_left": 2,
        "next_round_charge_cents": 0,
        "balance_cents": 10000,
        "can_afford_next_round": True,
        "owner_available": True,
    }
    cid = new_id("cnv")
    await settle(billing, agent, visitor, conversation_id=cid)
    await settle(billing, agent, visitor, conversation_id=cid)
    s = (await client.get(url, headers=h(visitor))).json()
    assert s["used_rounds"] == 2 and s["free_rounds_left"] == 0 and s["next_round_charge_cents"] == 150
    await open_wallet(visitor.uid, 100)
    s = (await client.get(url, headers=h(visitor))).json()
    assert s["can_afford_next_round"] is False and s["balance_cents"] == 100

    own = (await client.get(url, headers=h(owner))).json()
    assert own["is_owner"] is True and own["free_rounds_left"] is None and own["next_round_charge_cents"] == 0
    await open_wallet(owner.uid, 0)
    broke = (await client.get(url, headers=h(visitor))).json()
    assert broke["owner_available"] is False and broke["can_afford_next_round"] is False

    assert (await client.get(url)).status_code == 401
    assert (
        await client.get("/api/public/agents/no-such-agent-x1/billing", headers=h(visitor))
    ).status_code == 404
    await get_db().collection(Col.AGENTS).document(agent.id).update({"status": "paused"})
    paused = await client.get(url, headers=h(visitor))
    assert paused.status_code == 410 and paused.json()["code"] == "agent_offline"


async def test_billing_status_free_agent(client: httpx.AsyncClient) -> None:
    owner = await make_user("creator")
    visitor = await make_user()
    agent = await make_agent(owner, pricing={"mode": "free", "price_cents": 0, "free_rounds": 0})
    s = (await client.get(f"/api/public/agents/{agent.slug}/billing", headers=h(visitor))).json()
    assert s["billing_mode"] == "free" and s["price_cents"] == 0 and s["free_rounds_left"] is None
    assert s["next_round_charge_cents"] == 0 and s["can_afford_next_round"] is True


async def test_openapi_lists_billing_operations(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()
    ops = {op["operationId"] for path in spec["paths"].values() for op in path.values()}
    assert {
        "get_wallet",
        "list_ledger",
        "demo_topup",
        "get_dashboard",
        "get_agent_earnings",
        "get_billing_status",
    } <= ops
    assert any(re.fullmatch(r"/api/public/agents/\{slug\}/billing", p) for p in spec["paths"])
