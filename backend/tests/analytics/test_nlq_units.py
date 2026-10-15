"""Question flow with a fake warehouse and a scripted planner; transforms and MERGE SQL (SPEC §18 analytics)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from topskill.analytics import transform
from topskill.analytics.nlq import ask, chart_hint_for, fallback_answer
from topskill.analytics.tables import A2A_CALLS, AGENTS, LEDGER, RECORDS, ROUNDS, TABLES
from topskill.analytics.warehouse import DryRun, QueryResult, WarehouseError, build_merge_sql, json_safe

from .fakes import DATASET, FAST_LIMITS, LIMITS, PROJECT, FakeWarehouse, ScriptedPlanner, all_tables, plan

pytestmark = pytest.mark.unit

GOOD = "SELECT agent_title, SUM(amount_cents) AS income_cents FROM ledger GROUP BY 1 ORDER BY 2 DESC"
ROWS = QueryResult(
    [("agent_title", "STRING"), ("income_cents", "INTEGER")], [["Visa", 400], ["CV", 200]], False, 99
)


async def run(warehouse: FakeWarehouse, planner: ScriptedPlanner, question: str = "Who earned most?"):
    return await ask(question, "u_owner", warehouse=warehouse, planner=planner, limits=LIMITS)


async def test_answered_flow_wraps_sql_and_keeps_chart_hint() -> None:
    wh = FakeWarehouse(results=[ROWS])
    planner = ScriptedPlanner([plan(GOOD, "bar", "agent_title", "income_cents")])
    result = await run(wh, planner)
    assert result.outcome == "answered"
    assert result.sql == GOOD
    assert result.columns == [
        {"name": "agent_title", "type": "STRING"},
        {"name": "income_cents", "type": "INTEGER"},
    ]
    assert result.rows == [["Visa", 400], ["CV", 200]]
    assert result.answer == "You earned 1.60 credits."
    assert result.chart_hint.to_wire() == {"type": "bar", "x": "agent_title", "y": "income_cents"}
    kinds = [k for k, _ in wh.queries]
    assert kinds == ["dry", "run"]
    for _, sql in wh.queries:  # every executed statement is the owner-filtered wrapper
        assert sql.count("WHERE owner_uid = @uid") == len(TABLES)
        assert sql.rstrip().endswith(GOOD)


async def test_guard_rejection_gets_one_repair_attempt() -> None:
    wh = FakeWarehouse(results=[ROWS])
    planner = ScriptedPlanner([plan("SELECT * FROM topskill_analytics.fs_ledger"), plan(GOOD)])
    result = await run(wh, planner)
    assert result.outcome == "answered"
    assert planner.plan_calls[1][1] == "SELECT * FROM topskill_analytics.fs_ledger"
    assert "provided tables" in (planner.plan_calls[1][2] or "")
    assert [k for k, _ in wh.queries] == ["dry", "run"]  # the rejected SQL never reached BigQuery


async def test_two_unsafe_plans_end_rejected_without_touching_bigquery() -> None:
    wh = FakeWarehouse()
    planner = ScriptedPlanner([plan("DELETE FROM ledger WHERE TRUE"), plan("SELECT 1; DROP TABLE ledger")])
    result = await run(wh, planner)
    assert result.outcome == "rejected"
    assert result.rows == [] and wh.queries == []


async def test_invalid_query_from_dry_run_is_repaired() -> None:
    bad = WarehouseError("bad", user_message="Unrecognized name: earnings", invalid_query=True)
    wh = FakeWarehouse(dry_runs=[bad], results=[ROWS])
    planner = ScriptedPlanner([plan("SELECT earnings FROM ledger"), plan(GOOD)])
    result = await run(wh, planner)
    assert result.outcome == "answered"
    assert planner.plan_calls[1][2] == "Unrecognized name: earnings"


async def test_dry_run_referencing_other_tables_is_refused() -> None:
    wh = FakeWarehouse(dry_runs=[DryRun(10, "SELECT", (*all_tables(), "top-skill.other.secrets"))])
    result = await run(wh, ScriptedPlanner([plan(GOOD)]))
    assert result.outcome == "rejected"
    assert [k for k, _ in wh.queries] == ["dry"]


async def test_non_select_statement_type_is_refused() -> None:
    wh = FakeWarehouse(dry_runs=[DryRun(10, "SCRIPT", all_tables())])
    result = await run(wh, ScriptedPlanner([plan(GOOD)]))
    assert result.outcome == "rejected"


async def test_byte_cap_is_enforced_by_the_dry_run() -> None:
    wh = FakeWarehouse(dry_runs=[DryRun(LIMITS.max_bytes + 1, "SELECT", all_tables())])
    result = await run(wh, ScriptedPlanner([plan(GOOD)]))
    assert result.outcome == "rejected"
    assert "too much data" in result.answer
    assert [k for k, _ in wh.queries] == ["dry"]


async def test_cannot_answer_returns_the_models_reason() -> None:
    result = await run(FakeWarehouse(), ScriptedPlanner([plan("", cannot="I only see your agents' data.")]))
    assert result.outcome == "cannot_answer"
    assert result.answer == "I only see your agents' data."
    assert result.sql == ""


async def test_warehouse_outage_propagates() -> None:
    wh = FakeWarehouse(dry_runs=[WarehouseError("permission denied")])
    with pytest.raises(WarehouseError):
        await run(wh, ScriptedPlanner([plan(GOOD)]))


async def test_answer_model_failure_falls_back_to_plain_sentence() -> None:
    planner = ScriptedPlanner([plan("SELECT SUM(amount_cents) AS income_cents FROM ledger")])
    planner.answer_error = RuntimeError("boom")
    wh = FakeWarehouse(results=[QueryResult([("income_cents", "INTEGER")], [[160]], False, 8)])
    result = await run(wh, planner)
    assert result.outcome == "answered"
    assert result.answer == "The result is 1.60 credits."


async def test_slow_answer_model_falls_back_to_plain_sentence() -> None:
    planner = ScriptedPlanner([plan("SELECT SUM(amount_cents) AS income_cents FROM ledger")])
    planner.answer_delay_s = 5
    wh = FakeWarehouse(results=[QueryResult([("income_cents", "INTEGER")], [[160]], False, 8)])
    result = await ask("How much?", "u_owner", warehouse=wh, planner=planner, limits=FAST_LIMITS)
    assert result.outcome == "answered" and result.rows == [[160]]
    assert result.answer == "The result is 1.60 credits."


async def test_slow_sql_model_times_out_before_bigquery() -> None:
    planner = ScriptedPlanner([plan(GOOD), plan(GOOD)])
    planner.plan_delay_s = 5
    wh = FakeWarehouse()
    with pytest.raises(TimeoutError):
        await ask("How much?", "u_owner", warehouse=wh, planner=planner, limits=FAST_LIMITS)
    assert len(planner.plan_calls) == 1 and wh.queries == []


async def test_empty_result_skips_the_answer_model() -> None:
    planner = ScriptedPlanner([plan(GOOD)])
    wh = FakeWarehouse(results=[QueryResult([("agent_title", "STRING")], [], False, 0)])
    result = await run(wh, planner)
    assert result.answer == "There is no matching data yet."
    assert planner.answer_calls == 0


@pytest.mark.parametrize(
    ("chart", "x", "y", "expected"),
    [
        ("bar", "agent_title", "income_cents", "bar"),
        ("line", "agent_title", "income_cents", "line"),
        ("bar", "missing", "income_cents", "none"),
        ("bar", "agent_title", "agent_title", "none"),
        ("bar", "income_cents", "agent_title", "none"),  # y must be numeric
        ("none", "agent_title", "income_cents", "none"),
    ],
)
def test_chart_hint_validation(chart: str, x: str, y: str, expected: str) -> None:
    assert chart_hint_for(plan(GOOD, chart, x, y), ROWS).type == expected


def test_chart_hint_needs_two_rows() -> None:
    one = QueryResult(ROWS.columns, ROWS.rows[:1], False, 1)
    assert chart_hint_for(plan(GOOD, "bar", "agent_title", "income_cents"), one).type == "none"


def test_fallback_answers() -> None:
    assert fallback_answer(QueryResult([("n", "INTEGER")], [], False, 0)) == "There is no matching data yet."
    assert fallback_answer(QueryResult([("n", "INTEGER")], [[3]], False, 0)) == "The result is 3."
    assert fallback_answer(ROWS) == "Here are 2 rows that answer your question."


# --------------------------------------------------------------------------- transforms / SQL builders

NOW = datetime(2026, 10, 16, 8, 30, 0, 123456, tzinfo=UTC)


def test_ledger_row_maps_uid_to_owner_and_formats_timestamps() -> None:
    row = transform.ledger_row(
        LEDGER,
        "u1__a2a_income_x",
        {"uid": "u1", "category": "a2a_income", "amount_cents": 160, "created_at": NOW, "memo": "m" * 400},
    )
    assert row is not None
    assert row["owner_uid"] == "u1" and row["amount_cents"] == 160
    assert row["created_at"] == "2026-10-16T08:30:00.123456Z"
    assert len(row["memo"]) == 300
    assert set(row) == set(LEDGER.column_names) - {"synced_at"}


def test_rows_without_owner_or_time_are_skipped() -> None:
    assert transform.ledger_row(LEDGER, "x", {"created_at": NOW}) is None
    assert transform.a2a_call_row(A2A_CALLS, "x", {"owner_uid": "u1"}) is None


def test_round_row_counts_citations_tools_and_charge() -> None:
    conv = {"owner_uid": "own", "visitor_uid": "vis", "agent_id": "agt_1", "agent_slug": "visa"}
    msg = {
        "role": "model",
        "status": "ok",
        "created_at": NOW,
        "charge": {"charged_cents": 100, "free": False, "round_index": 3},
        "usage": {"model": "gemini-3.8-flash", "input_tokens": 10, "output_tokens": 5},
        "citations": [{"kind": "knowledge"}, {"kind": "web"}, {"kind": "web"}],
        "tools": [{"id": "1"}, {"id": "2"}],
    }
    row = transform.round_row(ROUNDS, "cnv_1", conv, "m_turn_1", msg)
    assert row is not None
    assert row["id"] == "cnv_1__m_turn_1"
    assert (row["counted"], row["is_free"], row["charged_cents"], row["round_index"]) == (True, False, 100, 3)
    assert (row["knowledge_citations"], row["web_citations"], row["tool_calls"]) == (1, 2, 2)
    assert row["by_owner"] is False
    assert transform.round_row(ROUNDS, "cnv_1", conv, "u_turn_1", {**msg, "role": "user"}) is None
    failed = transform.round_row(
        ROUNDS, "cnv_1", conv, "m_2", {"role": "model", "status": "failed", "created_at": NOW}
    )
    assert failed is not None and failed["counted"] is False and failed["charged_cents"] == 0


def test_record_row_copies_filled_keys_but_never_values() -> None:
    d = {
        "owner_uid": "own",
        "agent_id": "agt",
        "created_at": NOW,
        "data": {"full_name": {"value": "Ana Silva"}, "budget": {"value": None}, "email": {"value": "a@b.c"}},
    }
    row = transform.record_row(RECORDS, "agt__cnv", d)
    assert row is not None
    assert row["filled_keys"] == ["email", "full_name"] and row["fields_filled"] == 2
    assert "Ana Silva" not in str(row) and "a@b.c" not in str(row)


def test_agent_row_flattens_pricing() -> None:
    d = {
        "owner_uid": "own",
        "title": "Visa",
        "pricing": {"mode": "per_round", "price_cents": 100, "a2a_price_cents": 200, "a2a_enabled": True},
        "config": {"model": "gemini-3.8-flash"},
        "created_at": NOW,
    }
    row = transform.agent_row(AGENTS, "agt_1", d)
    assert row is not None and row["a2a_price_cents"] == 200 and row["deleted"] is False


def test_merge_sql_is_keyed_by_id_and_reads_json_rows() -> None:
    sql = build_merge_sql(RECORDS, project=PROJECT, dataset=DATASET)
    assert sql.startswith(f"MERGE `{PROJECT}.{DATASET}.fs_records` AS T")
    assert "FROM UNNEST(JSON_QUERY_ARRAY(@rows)) AS j" in sql
    assert "ON T.id = S.id" in sql
    assert "UPDATE SET owner_uid = S.owner_uid" in sql and "id = S.id," not in sql.split("UPDATE SET")[1]
    assert "CURRENT_TIMESTAMP() AS synced_at" in sql
    assert "ARRAY(SELECT JSON_VALUE(x) FROM UNNEST(JSON_QUERY_ARRAY(j, '$.filled_keys')) AS x)" in sql


def test_json_safe_values() -> None:
    import datetime as dt
    import decimal

    assert json_safe(decimal.Decimal("1.5")) == 1.5
    assert json_safe(dt.date(2026, 10, 16)) == "2026-10-16"
    assert json_safe(NOW) == "2026-10-16T08:30:00.123456Z"
    assert json_safe(float("nan")) is None
    assert json_safe(["a", 1]) == ["a", 1]
