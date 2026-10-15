"""BigQuery access for creator analytics (SPEC §18 analytics).

:class:`Warehouse` is the small interface the sync job and the question answering use; tests pass a fake.
:class:`BigQueryWarehouse` implements it with ``google-cloud-bigquery`` (a blocking client, so every call
runs in a worker thread).

Upserts are one ``MERGE`` per batch whose source is a JSON array passed as a single STRING query parameter
(``UNNEST(JSON_QUERY_ARRAY(@rows))``): no streaming buffer (rows are immediately mergeable and visible),
no staging tables, one job per table batch, and re-running a batch is a no-op apart from ``synced_at``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Protocol

from topskill.analytics.tables import TABLES, Column, TableSpec

log = logging.getLogger(__name__)

MERGE_BATCH_ROWS = 500
LABELS = {"app": "topskill", "feature": "analytics"}


@dataclass(frozen=True)
class DryRun:
    bytes_processed: int
    statement_type: str
    referenced_tables: tuple[str, ...]  # "project.dataset.table"


@dataclass(frozen=True)
class QueryResult:
    columns: list[tuple[str, str]]  # (name, BigQuery type)
    rows: list[list[Any]]  # JSON-safe values
    truncated: bool
    bytes_processed: int


class WarehouseError(RuntimeError):
    """BigQuery failed (permission, quota, timeout, invalid query). ``user_message`` is safe to show."""

    def __init__(self, message: str, *, user_message: str | None = None, invalid_query: bool = False):
        super().__init__(message)
        self.user_message = user_message or "The analytics warehouse is unavailable right now."
        self.invalid_query = invalid_query


class Warehouse(Protocol):
    project: str
    dataset: str

    async def ensure_schema(self) -> None: ...

    async def merge_rows(self, table: TableSpec, rows: list[dict[str, Any]]) -> int: ...

    async def dry_run(self, sql: str, uid: str) -> DryRun: ...

    async def query(
        self, sql: str, uid: str, *, max_bytes: int, timeout_s: float, max_rows: int
    ) -> QueryResult: ...


# --------------------------------------------------------------------------- SQL builders (pure)


def _bq_type(c: Column) -> str:
    return "STRING" if c.type == "STRING_ARRAY" else c.type


def _mode(c: Column) -> str:
    if c.type == "STRING_ARRAY":
        return "REPEATED"
    return "REQUIRED" if c.required else "NULLABLE"


def _extract(c: Column) -> str:
    path = f"'$.{c.name}'"
    if c.name == "synced_at":
        return "CURRENT_TIMESTAMP()"
    if c.type == "STRING":
        return f"JSON_VALUE(j, {path})"
    if c.type == "INT64":
        return f"SAFE_CAST(JSON_VALUE(j, {path}) AS INT64)"
    if c.type == "BOOL":
        return f"SAFE_CAST(JSON_VALUE(j, {path}) AS BOOL)"
    if c.type == "TIMESTAMP":
        return f"SAFE_CAST(JSON_VALUE(j, {path}) AS TIMESTAMP)"
    if c.type == "STRING_ARRAY":
        return f"ARRAY(SELECT JSON_VALUE(x) FROM UNNEST(JSON_QUERY_ARRAY(j, {path})) AS x)"
    raise ValueError(f"unknown column type {c.type}")


def build_merge_sql(table: TableSpec, *, project: str, dataset: str) -> str:
    """``MERGE`` of the JSON rows in ``@rows`` into ``table`` keyed by ``id`` (idempotent upsert)."""
    cols = table.column_names
    select = ",\n      ".join(f"{_extract(c)} AS {c.name}" for c in table.columns)
    updates = ", ".join(f"{c} = S.{c}" for c in cols if c != "id")
    insert_cols = ", ".join(cols)
    insert_vals = ", ".join(f"S.{c}" for c in cols)
    return (
        f"MERGE `{project}.{dataset}.{table.physical}` AS T\n"
        f"USING (\n  SELECT\n      {select}\n  FROM UNNEST(JSON_QUERY_ARRAY(@rows)) AS j\n) AS S\n"
        f"ON T.id = S.id\n"
        f"WHEN MATCHED THEN UPDATE SET {updates}\n"
        f"WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})"
    )


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """MERGE needs unique source keys: keep the last row per ``id`` (later reads are newer)."""
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_id[row["id"]] = row
    return list(by_id.values())


def json_safe(value: Any) -> Any:
    """BigQuery result value → JSON-serialisable value for the wire."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    return str(value)


# --------------------------------------------------------------------------- BigQuery implementation


class BigQueryWarehouse:
    """``Warehouse`` backed by BigQuery (ADC; project/location from settings)."""

    def __init__(self, *, project: str, dataset: str, location: str) -> None:
        self.project = project
        self.dataset = dataset
        self.location = location
        self._client: Any = None
        self._lock = threading.Lock()
        self._schema_ready = False

    def _bq(self) -> Any:
        from google.cloud import bigquery

        with self._lock:
            if self._client is None:
                self._client = bigquery.Client(project=self.project, location=self.location)
            return self._client

    # ---- schema

    def _ensure_schema_sync(self) -> None:
        from google.api_core import exceptions as gexc
        from google.cloud import bigquery

        client = self._bq()
        ds_ref = bigquery.DatasetReference(self.project, self.dataset)
        try:
            client.get_dataset(ds_ref)
        except gexc.NotFound:
            ds = bigquery.Dataset(ds_ref)
            ds.location = self.location
            ds.description = "TopSkill creator analytics (copied from Firestore by the analytics sync)."
            ds.labels = dict(LABELS)
            client.create_dataset(ds, exists_ok=True)
            log.info("analytics_dataset_created dataset=%s.%s", self.project, self.dataset)
        for spec in TABLES:
            schema = [
                bigquery.SchemaField(
                    c.name,
                    _bq_type(c),
                    mode=_mode(c),
                    description=c.description[:1024] or None,
                )
                for c in spec.columns
            ]
            ref = ds_ref.table(spec.physical)
            try:
                existing = client.get_table(ref)
            except gexc.NotFound:
                table = bigquery.Table(ref, schema=schema)
                table.time_partitioning = bigquery.TimePartitioning(
                    type_=bigquery.TimePartitioningType.DAY, field=spec.partition_field
                )
                table.clustering_fields = list(spec.cluster_fields)
                table.description = spec.description[:1024]
                table.labels = dict(LABELS)
                client.create_table(table, exists_ok=True)
                log.info("analytics_table_created table=%s", spec.physical)
                continue
            have = {f.name for f in existing.schema}
            missing = [f for f in schema if f.name not in have]
            if missing:  # additive schema evolution only (new nullable/repeated columns)
                existing.schema = list(existing.schema) + [
                    bigquery.SchemaField(
                        f.name, f.field_type, mode="REPEATED" if f.mode == "REPEATED" else "NULLABLE"
                    )
                    for f in missing
                ]
                client.update_table(existing, ["schema"])
                names = [f.name for f in missing]
                log.info("analytics_table_columns_added table=%s cols=%s", spec.physical, names)

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        try:
            await asyncio.to_thread(self._ensure_schema_sync)
        except Exception as exc:
            raise WarehouseError(f"ensure_schema failed: {exc}") from exc
        self._schema_ready = True

    # ---- upsert

    def _merge_sync(self, table: TableSpec, rows: list[dict[str, Any]]) -> int:
        from google.cloud import bigquery

        client = self._bq()
        sql = build_merge_sql(table, project=self.project, dataset=self.dataset)
        total = 0
        for start in range(0, len(rows), MERGE_BATCH_ROWS):
            batch = rows[start : start + MERGE_BATCH_ROWS]
            config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("rows", "STRING", json.dumps(batch, ensure_ascii=False))
                ],
                labels=dict(LABELS),
                use_legacy_sql=False,
            )
            job = client.query(sql, job_config=config)
            job.result(timeout=120)
            total += int(job.num_dml_affected_rows or 0)
        return total

    async def merge_rows(self, table: TableSpec, rows: list[dict[str, Any]]) -> int:
        rows = dedupe_rows(rows)
        if not rows:
            return 0
        try:
            return await asyncio.to_thread(self._merge_sync, table, rows)
        except Exception as exc:
            raise WarehouseError(f"merge into {table.physical} failed: {exc}") from exc

    # ---- queries

    def _uid_config(self, uid: str, **kwargs: Any) -> Any:
        from google.cloud import bigquery

        return bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("uid", "STRING", uid)],
            use_legacy_sql=False,
            labels=dict(LABELS),
            **kwargs,
        )

    def _dry_run_sync(self, sql: str, uid: str) -> DryRun:
        job = self._bq().query(sql, job_config=self._uid_config(uid, dry_run=True, use_query_cache=False))
        refs = tuple(f"{t.project}.{t.dataset_id}.{t.table_id}" for t in (job.referenced_tables or []))
        return DryRun(
            bytes_processed=int(job.total_bytes_processed or 0),
            statement_type=str(job.statement_type or ""),
            referenced_tables=refs,
        )

    async def dry_run(self, sql: str, uid: str) -> DryRun:
        from google.api_core import exceptions as gexc

        try:
            return await asyncio.to_thread(self._dry_run_sync, sql, uid)
        except gexc.BadRequest as exc:
            raise WarehouseError(
                f"dry run rejected: {exc}", user_message=_bad_request_message(exc), invalid_query=True
            ) from exc
        except Exception as exc:
            raise WarehouseError(f"dry run failed: {exc}") from exc

    def _query_sync(self, sql: str, uid: str, max_bytes: int, timeout_s: float, max_rows: int) -> QueryResult:
        config = self._uid_config(
            uid, maximum_bytes_billed=max_bytes, job_timeout_ms=int(timeout_s * 1000), use_query_cache=True
        )
        job = self._bq().query(sql, job_config=config)
        it = job.result(timeout=timeout_s, max_results=max_rows + 1)
        columns = [(f.name, f.field_type + ("[]" if f.mode == "REPEATED" else "")) for f in it.schema]
        rows: list[list[Any]] = []
        truncated = False
        for row in it:
            if len(rows) >= max_rows:
                truncated = True
                break
            rows.append([json_safe(v) for v in row.values()])
        return QueryResult(
            columns=columns,
            rows=rows,
            truncated=truncated,
            bytes_processed=int(job.total_bytes_processed or 0),
        )

    async def query(
        self, sql: str, uid: str, *, max_bytes: int, timeout_s: float, max_rows: int
    ) -> QueryResult:
        from google.api_core import exceptions as gexc

        try:
            return await asyncio.to_thread(self._query_sync, sql, uid, max_bytes, timeout_s, max_rows)
        except gexc.BadRequest as exc:
            raise WarehouseError(
                f"query rejected: {exc}", user_message=_bad_request_message(exc), invalid_query=True
            ) from exc
        except Exception as exc:
            raise WarehouseError(f"query failed: {exc}") from exc


def _bad_request_message(exc: Exception) -> str:
    """BigQuery's own message for an invalid query (fed back to the model for one repair attempt)."""
    errors = getattr(exc, "errors", None) or []
    for err in errors:
        message = err.get("message") if isinstance(err, dict) else None
        if message:
            return str(message)[:500]
    return str(getattr(exc, "message", exc))[:500]
