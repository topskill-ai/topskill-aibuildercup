"""Test doubles for the analytics package: an in-memory warehouse and a scripted planner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from topskill.analytics.nlq import AskLimits, Planner, SqlPlan
from topskill.analytics.service import Analytics
from topskill.analytics.sync import MemorySyncState, SyncState
from topskill.analytics.tables import TABLES, TableSpec
from topskill.analytics.warehouse import DryRun, QueryResult, WarehouseError, dedupe_rows

PROJECT = "top-skill"
DATASET = "topskill_analytics_test"


def all_tables() -> tuple[str, ...]:
    return tuple(f"{PROJECT}.{DATASET}.{t.physical}" for t in TABLES)


@dataclass
class FakeWarehouse:
    """Rows per physical table keyed by id (``MERGE`` semantics); scripted dry runs and results."""

    project: str = PROJECT
    dataset: str = DATASET
    tables: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    merges: list[tuple[str, int]] = field(default_factory=list)
    schema_calls: int = 0
    dry_runs: list[DryRun | WarehouseError] = field(default_factory=list)
    results: list[QueryResult | WarehouseError] = field(default_factory=list)
    queries: list[tuple[str, str]] = field(default_factory=list)
    fail_merge: bool = False

    async def ensure_schema(self) -> None:
        self.schema_calls += 1

    async def merge_rows(self, table: TableSpec, rows: list[dict[str, Any]]) -> int:
        if self.fail_merge:
            raise WarehouseError("merge failed (fake)")
        rows = dedupe_rows(rows)
        store = self.tables.setdefault(table.physical, {})
        for row in rows:
            assert set(row) == set(table.column_names) - {"synced_at"}, f"bad row keys for {table.physical}"
            store[row["id"]] = dict(row)
        self.merges.append((table.physical, len(rows)))
        return len(rows)

    def rows_for(self, physical: str, owner_uid: str) -> dict[str, dict[str, Any]]:
        return {k: v for k, v in self.tables.get(physical, {}).items() if v["owner_uid"] == owner_uid}

    async def dry_run(self, sql: str, uid: str) -> DryRun:
        self.queries.append(("dry", sql))
        item = self.dry_runs.pop(0) if self.dry_runs else DryRun(1024, "SELECT", all_tables())
        if isinstance(item, WarehouseError):
            raise item
        return item

    async def query(
        self, sql: str, uid: str, *, max_bytes: int, timeout_s: float, max_rows: int
    ) -> QueryResult:
        self.queries.append(("run", sql))
        item = self.results.pop(0) if self.results else QueryResult([("n", "INTEGER")], [[1]], False, 10)
        if isinstance(item, WarehouseError):
            raise item
        return item


class ScriptedPlanner(Planner):
    def __init__(self, plans: list[SqlPlan], answer: str = "You earned 1.60 credits.") -> None:
        super().__init__(["fake-model"])
        self.plans = list(plans)
        self.answer_text = answer
        self.plan_calls: list[tuple[str, str | None, str | None]] = []
        self.answer_calls = 0
        self.answer_error: Exception | None = None

    async def plan(self, question: str, *, previous: str | None, error: str | None) -> tuple[SqlPlan, str]:
        self.plan_calls.append((question, previous, error))
        return self.plans.pop(0), "fake-model"

    async def answer(self, question: str, sql: str, result: QueryResult) -> str:
        self.answer_calls += 1
        if self.answer_error is not None:
            raise self.answer_error
        return self.answer_text


def plan(sql: str, chart: str = "none", x: str = "", y: str = "", cannot: str = "") -> SqlPlan:
    return SqlPlan(sql=sql, chart=chart, x=x, y=y, cannot_answer=cannot)  # type: ignore[arg-type]


LIMITS = AskLimits(max_bytes=100 * 1024 * 1024, timeout_s=5, max_rows=200)


def make_analytics(
    warehouse: FakeWarehouse | None = None,
    planner: ScriptedPlanner | None = None,
    *,
    state: SyncState | None = None,
    enabled: bool = True,
) -> Analytics:
    return Analytics(
        warehouse=warehouse or FakeWarehouse(),
        state=state or MemorySyncState(),
        planner=planner or ScriptedPlanner([]),
        limits=LIMITS,
        enabled=enabled,
        sync_min_interval_s=60,
        sync_wait_s=5,
    )
