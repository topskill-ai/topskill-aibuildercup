"""Analytics endpoints with a fake warehouse/planner (SPEC §18 analytics)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.conftest import dev_headers
from topskill.analytics import router as analytics_router
from topskill.analytics.service import analytics_available, get_analytics
from topskill.analytics.warehouse import QueryResult, WarehouseError
from topskill.config import Settings, get_settings
from topskill.testing.factories import unique

from .fakes import FAST_LIMITS, FakeWarehouse, ScriptedPlanner, make_analytics, plan

pytestmark = pytest.mark.emulator

SQL = "SELECT agent_title, SUM(amount_cents) AS income_cents FROM ledger GROUP BY 1"


@pytest.fixture
def use(app) -> Iterator:
    def install(analytics):
        app.dependency_overrides[get_analytics] = lambda: analytics
        return analytics

    yield install
    app.dependency_overrides.pop(get_analytics, None)


def creator() -> dict[str, str]:
    return dev_headers(unique("cr_"), "creator")


async def test_status_lists_suggestions(client, use) -> None:
    use(make_analytics())
    resp = await client.get("/api/analytics/status", headers=creator())
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert "Which agent earned the most from A2A this week?" in body["suggestions"]
    assert body["data_as_of"] is None


async def test_visitors_get_creator_required(client, use) -> None:
    use(make_analytics())
    resp = await client.post(
        "/api/analytics/ask", json={"question": "How much?"}, headers=dev_headers(unique("v_"), "visitor")
    )
    assert resp.status_code == 403 and resp.json()["code"] == "creator_required"


async def test_ask_returns_answer_sql_rows_and_chart(client, use) -> None:
    wh = FakeWarehouse(
        results=[
            QueryResult(
                [("agent_title", "STRING"), ("income_cents", "INTEGER")], [["A", 3], ["B", 1]], False, 7
            )
        ]
    )
    planner = ScriptedPlanner([plan(SQL, "bar", "agent_title", "income_cents")], answer="A earned the most.")
    analytics = use(make_analytics(wh, planner))
    headers = creator()
    resp = await client.post(
        "/api/analytics/ask", json={"question": "  Which   agent earned most? "}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["question"] == "Which agent earned most?"
    assert body["outcome"] == "answered"
    assert body["sql"] == SQL
    assert body["columns"] == [
        {"name": "agent_title", "type": "STRING"},
        {"name": "income_cents", "type": "INTEGER"},
    ]
    assert body["rows"] == [["A", 3], ["B", 1]]
    assert body["answer"] == "A earned the most."
    assert body["chart_hint"] == {"type": "bar", "x": "agent_title", "y": "income_cents"}
    assert body["data_as_of"] is not None  # the on-demand sync ran (memory state) before the question
    assert wh.schema_calls == 1
    uid = headers["X-Dev-User"]
    dry_sql = wh.queries[0][1]
    assert "WHERE owner_uid = @uid" in dry_sql and uid not in dry_sql  # uid travels only as a parameter
    assert analytics.enabled


async def test_ask_validates_question(client, use) -> None:
    use(make_analytics())
    resp = await client.post("/api/analytics/ask", json={"question": "x"}, headers=creator())
    assert resp.status_code == 400 and resp.json()["code"] == "validation_error"
    resp = await client.post("/api/analytics/ask", json={"question": "     "}, headers=creator())
    assert resp.status_code == 400 and resp.json()["code"] == "validation_error"


async def test_disabled_feature_is_503_but_status_still_answers(client, use) -> None:
    use(make_analytics(enabled=False))
    resp = await client.post("/api/analytics/ask", json={"question": "How much?"}, headers=creator())
    assert resp.status_code == 503 and resp.json()["code"] == "service_unavailable"
    status = await client.get("/api/analytics/status", headers=creator())
    assert status.status_code == 200 and status.json()["enabled"] is False


async def test_warehouse_outage_is_503_and_sync_failure_is_fail_open(client, use) -> None:
    wh = FakeWarehouse(fail_merge=True, dry_runs=[WarehouseError("permission denied")])
    use(make_analytics(wh, ScriptedPlanner([plan(SQL)])))
    resp = await client.post("/api/analytics/ask", json={"question": "How much?"}, headers=creator())
    # the failed on-demand sync did not block the question; the outage of the query itself is a 503
    assert resp.status_code == 503 and resp.json()["code"] == "service_unavailable"
    assert resp.json()["trace_id"]


async def test_slow_model_is_a_503_not_a_hang(client, use) -> None:
    planner = ScriptedPlanner([plan(SQL)])
    planner.plan_delay_s = 5
    use(make_analytics(planner=planner, limits=FAST_LIMITS))
    resp = await client.post("/api/analytics/ask", json={"question": "How much?"}, headers=creator())
    assert resp.status_code == 503 and resp.json()["code"] == "service_unavailable"
    assert "too long" in resp.json()["message"] and resp.json()["trace_id"]


async def test_rate_limit_per_creator(client, use, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "analytics_ask_per_hour", 1)
    use(make_analytics(planner=ScriptedPlanner([plan(SQL), plan(SQL)])))
    headers = creator()
    first = await client.post("/api/analytics/ask", json={"question": "How much?"}, headers=headers)
    assert first.status_code == 200
    second = await client.post("/api/analytics/ask", json={"question": "How much?"}, headers=headers)
    assert second.status_code == 429 and second.json()["code"] == "rate_limited"
    other = await client.post("/api/analytics/ask", json={"question": "How much?"}, headers=creator())
    assert other.status_code == 200


async def test_sync_endpoint_dev_bypass_and_token_rules(client, use, monkeypatch) -> None:
    wh = FakeWarehouse()
    use(make_analytics(wh))
    resp = await client.post("/api/internal/analytics/sync", headers=dev_headers(unique("ops_")))
    assert resp.status_code == 200, resp.text
    assert resp.json()["ran"] is True and "ledger" in resp.json()["sources"]

    assert (await client.post("/api/internal/analytics/sync")).status_code == 401

    def fake_verify(token: str, audience: str) -> dict:
        if token != "good":
            raise ValueError("bad token")
        return {"email": "topskill-scheduler@top-skill.iam.gserviceaccount.com", "email_verified": True}

    monkeypatch.setattr(analytics_router, "_verify_google_id_token", fake_verify)
    bad = await client.post("/api/internal/analytics/sync", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    not_allowed = await client.post("/api/internal/analytics/sync", headers={"Authorization": "Bearer good"})
    assert not_allowed.status_code == 403 and not_allowed.json()["code"] == "forbidden"
    monkeypatch.setattr(
        get_settings(), "analytics_sync_invokers", ["topskill-scheduler@top-skill.iam.gserviceaccount.com"]
    )
    allowed = await client.post(
        "/api/internal/analytics/sync?full=true", headers={"Authorization": "Bearer good"}
    )
    assert allowed.status_code == 200 and allowed.json()["ran"] is True


def test_feature_flag_never_uses_the_prod_dataset_outside_prod() -> None:
    base = {"mandate_signing_key": "x"}
    assert not analytics_available(Settings(env="test", analytics_enabled=False, **base))
    assert not analytics_available(Settings(env="dev", analytics_enabled=True, **base))
    assert analytics_available(
        Settings(env="dev", analytics_enabled=True, analytics_dataset="topskill_analytics_dev")
    )
    assert analytics_available(Settings(env="prod", analytics_enabled=True, **base))


def test_router_is_mounted_under_api() -> None:
    from topskill.main import create_app

    app = create_app()
    assert "topskill.analytics.router:router" in app.state.mounted_routers
    paths = set(app.openapi()["paths"])
    assert {"/api/analytics/status", "/api/analytics/ask", "/api/internal/analytics/sync"} <= paths
