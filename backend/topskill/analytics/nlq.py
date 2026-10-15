"""Natural-language questions over the creator's own data (SPEC §18.4).

``ask(question, uid)``:
1. Gemini (structured output) writes ONE GoogleSQL SELECT over the CTE names of :mod:`.tables`, plus a
   chart hint, or says the question cannot be answered from these tables;
2. :func:`sql_guard.check_sql` rejects anything but a single safe SELECT; :func:`sql_guard.wrap_sql`
   pre-filters every table to ``owner_uid = @uid``;
3. a BigQuery dry run must report a SELECT that references only the analytics tables and scans at most
   ``ANALYTICS_MAX_BYTES``; the real run has ``maximum_bytes_billed``, a job timeout and a row cap;
4. an invalid query (guard or BigQuery syntax/semantic error) gets ONE repair attempt with the error;
5. Gemini writes a 1-3 sentence answer from the rows (falls back to a plain sentence if that call fails
   or takes longer than ``ANALYTICS_ANSWER_TIMEOUT_S``).

Each SQL-writing call is bounded by ``ANALYTICS_PLAN_TIMEOUT_S`` (``TimeoutError`` propagates; the router
also bounds the whole question by ``ANALYTICS_ASK_TIMEOUT_S`` and answers 503 on either).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from topskill.analytics import sql_guard
from topskill.analytics.tables import schema_prompt
from topskill.analytics.warehouse import QueryResult, Warehouse, WarehouseError
from topskill.db import utcnow

log = logging.getLogger(__name__)

ChartType = Literal["bar", "line", "none"]
Outcome = Literal["answered", "cannot_answer", "rejected"]

MAX_ANSWER_ROWS = 40  # rows shown to the answer model
MAX_CHART_POINTS = 60
NUMERIC_TYPES = frozenset({"INTEGER", "INT64", "FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"})

SUGGESTED_QUESTIONS: tuple[str, ...] = (
    "Which agent earned the most from A2A this week?",
    "How much did I earn per day over the last 14 days?",
    "How many paid vs free rounds did each agent have this month?",
    "Which agents had the most failed A2A calls, and why?",
    "How many records were completed per agent?",
)


class SqlPlan(BaseModel):
    sql: str = Field(
        description="ONE GoogleSQL SELECT (may start with WITH) over the listed tables only; empty string "
        "when the question cannot be answered from these tables."
    )
    chart: ChartType = Field(
        description="'line' for a time series (x is a date/time column), 'bar' for a comparison across "
        "categories (x is a label column), 'none' for a single number or a list."
    )
    x: str = Field(description="Result column for the chart's x axis, or empty string.")
    y: str = Field(description="Numeric result column for the chart's y axis, or empty string.")
    cannot_answer: str = Field(
        description="When sql is empty: one short sentence telling the creator what you can answer instead."
    )


class AnswerText(BaseModel):
    answer: str = Field(description="1-3 plain sentences that answer the question from the rows.")


@dataclass(frozen=True)
class ChartHint:
    type: ChartType
    x: str | None
    y: str | None

    def to_wire(self) -> dict[str, Any]:
        return {"type": self.type, "x": self.x, "y": self.y}


NO_CHART = ChartHint("none", None, None)


@dataclass(frozen=True)
class AskResult:
    question: str
    outcome: Outcome
    sql: str
    columns: list[dict[str, str]]
    rows: list[list[Any]]
    answer: str
    chart_hint: ChartHint
    truncated: bool
    bytes_processed: int
    model: str | None

    def to_wire(self, data_as_of: datetime | None) -> dict[str, Any]:
        from topskill.analytics.transform import ts

        return {
            "question": self.question,
            "outcome": self.outcome,
            "sql": self.sql,
            "columns": self.columns,
            "rows": self.rows,
            "answer": self.answer,
            "chart_hint": self.chart_hint.to_wire(),
            "truncated": self.truncated,
            "bytes_processed": self.bytes_processed,
            "model": self.model,
            "data_as_of": ts(data_as_of) if data_as_of else None,
        }


@dataclass(frozen=True)
class AskLimits:
    max_bytes: int
    timeout_s: float  # BigQuery job timeout
    max_rows: int
    plan_timeout_s: float = 25.0  # one SQL-writing model call (its own fallback chain and retries)
    answer_timeout_s: float = 15.0  # the summary call; on timeout the plain fallback sentence is used
    ask_timeout_s: float = 60.0  # the whole question (applied by the router)


# --------------------------------------------------------------------------- prompts

SQL_SYSTEM = """You write BigQuery GoogleSQL for a creator who sells AI agents on TopSkill.
Answer the creator's question with ONE read-only SELECT statement over ONLY these tables (they already
contain only this creator's data; never filter by owner, never qualify names with a project or dataset):

{schema}

Rules:
- Exactly one statement: SELECT ... or WITH ... SELECT .... No semicolons, comments, backticks, query
  parameters (@...), DDL/DML, scripting, TABLE/MODEL functions, INFORMATION_SCHEMA or other tables.
- Money columns are integer cents (100 cents = 1 credit). Keep money results in cents and end their alias
  with _cents (e.g. income_cents); the UI formats them as credits.
- "Earned" / "income" = SUM(amount_cents) of ledger rows with category IN ('creator_income', 'a2a_income');
  web visitor income = 'creator_income'; A2A income = 'a2a_income'.
- Times are UTC TIMESTAMPs. Today is {today} (UTC). "This week" = since DATE_TRUNC(CURRENT_DATE(),
  WEEK(MONDAY)); "last N days" = created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL N DAY).
  Group time series by DATE(created_at) AS day and order by day.
- Prefer agent titles for labels (ledger.agent_title, a2a_calls.agent_title, or JOIN agents ON agents.id =
  ... .agent_id and use agents.title).
- Use short snake_case column aliases, ORDER BY the most useful column, and LIMIT 50 unless the question
  asks for a single number.
- If the tables cannot answer the question, return an empty sql and explain in cannot_answer."""

ANSWER_SYSTEM = """You explain analytics results to a creator in 1-3 short plain sentences (no Markdown, no
SQL). Use only the rows given. Columns ending in _cents are integer cents: write them as credits with two
decimals (e.g. 160 -> 1.60 credits). If there are no rows, say that there is no matching data yet."""


def _sql_prompt(question: str, *, previous: str | None, error: str | None) -> str:
    prompt = f"Question: {question.strip()}"
    if previous is not None:
        prompt += (
            f"\n\nYour previous SQL was rejected.\nSQL:\n{previous}\nError: {error}\n"
            "Write a corrected query that follows every rule."
        )
    return prompt


def _answer_prompt(question: str, sql: str, result: QueryResult) -> str:
    names = [name for name, _ in result.columns]
    rows = [dict(zip(names, row, strict=False)) for row in result.rows[:MAX_ANSWER_ROWS]]
    more = len(result.rows) - len(rows)
    note = " (more rows omitted)" if more > 0 or result.truncated else ""
    return (
        f"Question: {question.strip()}\nSQL: {sql}\nRows{note} ({len(result.rows)} total):\n"
        f"{json.dumps(rows, ensure_ascii=False, default=str)[:12000]}"
    )


# --------------------------------------------------------------------------- helpers


def chart_hint_for(plan: SqlPlan, result: QueryResult) -> ChartHint:
    """Keep the model's hint only when it fits the result: x and y are columns, y is numeric, 2+ rows."""
    if plan.chart == "none" or len(result.rows) < 2 or len(result.rows) > MAX_CHART_POINTS:
        return NO_CHART
    types = {name: type_ for name, type_ in result.columns}
    x, y = plan.x.strip(), plan.y.strip()
    if x not in types or y not in types or x == y:
        return NO_CHART
    if types[y] not in NUMERIC_TYPES:
        return NO_CHART
    return ChartHint(plan.chart, x, y)


def fallback_answer(result: QueryResult) -> str:
    if not result.rows:
        return "There is no matching data yet."
    if len(result.rows) == 1 and len(result.columns) == 1:
        name, _ = result.columns[0]
        value = result.rows[0][0]
        if name.endswith("_cents") and isinstance(value, int | float):
            return f"The result is {value / 100:.2f} credits."
        return f"The result is {value}."
    if len(result.rows) == 1:
        return "Here is the row that answers your question."
    return f"Here are {len(result.rows)} rows that answer your question."


_WS_RE = re.compile(r"\s+")


def _clean_answer(text: str) -> str:
    return _WS_RE.sub(" ", text or "").strip()[:600]


# --------------------------------------------------------------------------- the flow


class Planner:
    """Gemini calls (separate so tests can script them)."""

    def __init__(self, chain: list[str]) -> None:
        self.chain = chain

    async def plan(self, question: str, *, previous: str | None, error: str | None) -> tuple[SqlPlan, str]:
        from topskill.gemini import generate_structured

        system = SQL_SYSTEM.format(schema=schema_prompt(), today=utcnow().date().isoformat())
        return await generate_structured(
            self.chain,
            _sql_prompt(question, previous=previous, error=error),
            SqlPlan,
            system_instruction=system,
            thinking="low",
        )

    async def answer(self, question: str, sql: str, result: QueryResult) -> str:
        from topskill.gemini import generate_structured

        parsed, _ = await generate_structured(
            self.chain,
            _answer_prompt(question, sql, result),
            AnswerText,
            system_instruction=ANSWER_SYSTEM,
            thinking="low",  # gemini-3.8-flash rejects THINKING_LEVEL_MINIMAL
        )
        return _clean_answer(parsed.answer)


async def ask(
    question: str, uid: str, *, warehouse: Warehouse, planner: Planner, limits: AskLimits
) -> AskResult:
    """Answer ``question`` from ``uid``'s rows only. Raises ``WarehouseError`` when BigQuery is down and
    ``ApiError(502)`` when the model chain fails; an unsafe/invalid query ends as ``outcome="rejected"``."""
    project, dataset = warehouse.project, warehouse.dataset
    allowed = sql_guard.allowed_tables(project=project, dataset=dataset)
    previous: str | None = None
    error: str | None = None
    model_used: str | None = None
    last_sql = ""
    for _attempt in range(2):
        async with asyncio.timeout(limits.plan_timeout_s):
            plan, model_used = await planner.plan(question, previous=previous, error=error)
        last_sql = (plan.sql or "").strip()
        if not last_sql:
            reason = _clean_answer(plan.cannot_answer) or (
                "I can answer questions about your earnings, rounds, conversations, A2A calls, records "
                "and agents."
            )
            return AskResult(question, "cannot_answer", "", [], [], reason, NO_CHART, False, 0, model_used)
        try:
            checked = sql_guard.check_sql(last_sql, dataset=dataset, project=project)
        except sql_guard.SqlRejected as exc:
            log.warning("analytics_sql_rejected reason=%s", exc.reason)
            previous, error = last_sql, exc.message
            continue
        wrapped = sql_guard.wrap_sql(checked, project=project, dataset=dataset)
        try:
            dry = await warehouse.dry_run(wrapped, uid)
        except WarehouseError as exc:
            if not exc.invalid_query:
                raise
            log.warning("analytics_dry_run_invalid error=%s", exc.user_message[:200])
            previous, error = checked, exc.user_message
            continue
        if dry.statement_type.upper() != "SELECT" or not set(dry.referenced_tables) <= allowed:
            log.error(
                "analytics_dry_run_refused statement=%s tables=%s", dry.statement_type, dry.referenced_tables
            )
            return _rejected(question, checked, model_used, "That question needs data I cannot query.")
        if dry.bytes_processed > limits.max_bytes:
            return _rejected(
                question, checked, model_used, "That question would scan too much data. Try a shorter period."
            )
        try:
            result = await warehouse.query(
                wrapped, uid, max_bytes=limits.max_bytes, timeout_s=limits.timeout_s, max_rows=limits.max_rows
            )
        except WarehouseError as exc:
            if not exc.invalid_query:
                raise
            log.warning("analytics_query_invalid error=%s", exc.user_message[:200])
            return _rejected(question, checked, model_used, "I could not run that query. Try rephrasing.")
        answer = ""
        if result.rows:
            try:  # the rows are the answer; a failed or slow summary must not lose them
                async with asyncio.timeout(limits.answer_timeout_s):
                    answer = await planner.answer(question, checked, result)
            except Exception as exc:  # TimeoutError included
                log.warning("analytics_answer_failed error=%s", type(exc).__name__)
                answer = ""
        return AskResult(
            question=question,
            outcome="answered",
            sql=checked,
            columns=[{"name": n, "type": t} for n, t in result.columns],
            rows=result.rows,
            answer=answer or fallback_answer(result),
            chart_hint=chart_hint_for(plan, result),
            truncated=result.truncated,
            bytes_processed=result.bytes_processed,
            model=model_used,
        )
    return _rejected(
        question, last_sql, model_used, "I couldn't turn that into a safe query. Try rephrasing the question."
    )


def _rejected(question: str, sql: str, model: str | None, message: str) -> AskResult:
    return AskResult(question, "rejected", sql, [], [], message, NO_CHART, False, 0, model)
