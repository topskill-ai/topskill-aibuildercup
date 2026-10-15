"""Incremental Firestore → BigQuery sync (SPEC §18 analytics).

Billing never writes to the warehouse (a write-through after settlement could fail after the money moved).
Instead this job copies documents, idempotently and incrementally:

* per source a **watermark** = the largest ``created_at`` / ``updated_at`` already copied, kept in
  ``analytics_sync/{key}``; a run reads ``field >= watermark - overlap`` ordered by that field (single-field
  range queries, no composite index), so documents committed late with an older timestamp are still
  picked up, and re-reading a document is harmless because every write is a ``MERGE`` keyed by doc id;
* ``rounds`` come from the model messages of every conversation whose ``updated_at`` moved (each turn bumps
  it in the settlement transaction, §5.4 T2), read per conversation (≤ 62 messages each);
* ``a2a_calls`` have no ``updated_at``: they are re-read for ``A2A_LOOKBACK`` after creation, which covers
  the ``running`` → ``completed``/``failed`` transition (calls settle within ``A2A_CALL_TIMEOUT_S``);
* a run is serialised by a lease in ``analytics_sync/_lease`` (one sync at a time across instances) and
  by an in-process lock; :func:`sync_if_stale` skips when the last run finished less than
  ``ANALYTICS_SYNC_MIN_INTERVAL_S`` ago (on-demand throttle when creators ask questions).

Deletions are not propagated (Firestore soft-deletes agents; ledger, calls and messages are append-only).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from topskill.analytics import transform
from topskill.analytics.tables import A2A_CALLS, AGENTS, CONVERSATIONS, LEDGER, RECORDS, ROUNDS, TableSpec
from topskill.analytics.warehouse import Warehouse
from topskill.db import Col, get_db, utcnow

log = logging.getLogger(__name__)

STATE_COLLECTION = "analytics_sync"
EPOCH = datetime(2020, 1, 1, tzinfo=UTC)
OVERLAP = timedelta(minutes=2)
A2A_LOOKBACK = timedelta(hours=1)
PAGE_SIZE = 500
MAX_ROWS_PER_SOURCE = 20_000  # per run; the next run continues from the watermark
LEASE_TTL = timedelta(minutes=5)
MESSAGE_READ_CONCURRENCY = 8


# --------------------------------------------------------------------------- state


class SyncState(Protocol):
    async def get_watermark(self, key: str) -> datetime | None: ...
    async def set_watermark(self, key: str, value: datetime, rows: int) -> None: ...
    async def try_lease(self, owner: str, ttl: timedelta) -> bool: ...
    async def release_lease(self, owner: str, *, finished: bool) -> None: ...
    async def last_finished_at(self) -> datetime | None: ...


class FirestoreSyncState:
    """Watermarks in ``{collection}/{prefix}{source}``; lease and last run in ``…/{prefix}_lease``."""

    def __init__(self, collection: str = STATE_COLLECTION, prefix: str = "") -> None:
        self.collection = collection
        self.prefix = prefix

    def _ref(self, key: str) -> Any:
        return get_db().collection(self.collection).document(f"{self.prefix}{key}")

    async def get_watermark(self, key: str) -> datetime | None:
        snap = await self._ref(key).get()
        value = (snap.to_dict() or {}).get("watermark") if snap.exists else None
        return value if isinstance(value, datetime) else None

    async def set_watermark(self, key: str, value: datetime, rows: int) -> None:
        await self._ref(key).set(
            {"watermark": value, "rows": firestore.Increment(rows), "updated_at": utcnow()}, merge=True
        )

    async def try_lease(self, owner: str, ttl: timedelta) -> bool:
        ref = self._ref("_lease")

        @firestore.async_transactional
        async def claim(tx: Any) -> bool:
            snap = await ref.get(transaction=tx)
            data = (snap.to_dict() or {}) if snap.exists else {}
            until = data.get("lease_until")
            now = utcnow()
            if isinstance(until, datetime) and until > now and data.get("lease_owner") != owner:
                return False
            tx.set(ref, {"lease_owner": owner, "lease_until": now + ttl}, merge=True)
            return True

        return await claim(get_db().transaction())

    async def release_lease(self, owner: str, *, finished: bool) -> None:
        ref = self._ref("_lease")

        @firestore.async_transactional
        async def release(tx: Any) -> None:
            snap = await ref.get(transaction=tx)
            data = (snap.to_dict() or {}) if snap.exists else {}
            if data.get("lease_owner") not in (None, owner):
                return
            updates: dict[str, Any] = {"lease_owner": None, "lease_until": None}
            if finished:
                updates["last_finished_at"] = utcnow()
            tx.set(ref, updates, merge=True)

        await release(get_db().transaction())

    async def last_finished_at(self) -> datetime | None:
        snap = await self._ref("_lease").get()
        value = (snap.to_dict() or {}).get("last_finished_at") if snap.exists else None
        return value if isinstance(value, datetime) else None


class MemorySyncState:
    """In-memory state (scripts and unit tests)."""

    def __init__(self) -> None:
        self.watermarks: dict[str, datetime] = {}
        self.lease: tuple[str, datetime] | None = None
        self.finished_at: datetime | None = None

    async def get_watermark(self, key: str) -> datetime | None:
        return self.watermarks.get(key)

    async def set_watermark(self, key: str, value: datetime, rows: int) -> None:
        self.watermarks[key] = value

    async def try_lease(self, owner: str, ttl: timedelta) -> bool:
        now = utcnow()
        if self.lease and self.lease[1] > now and self.lease[0] != owner:
            return False
        self.lease = (owner, now + ttl)
        return True

    async def release_lease(self, owner: str, *, finished: bool) -> None:
        if self.lease and self.lease[0] == owner:
            self.lease = None
        if finished:
            self.finished_at = utcnow()

    async def last_finished_at(self) -> datetime | None:
        return self.finished_at


# --------------------------------------------------------------------------- report


@dataclass
class SourceReport:
    read: int = 0
    written: int = 0
    watermark: str | None = None


@dataclass
class SyncReport:
    ran: bool
    reason: str = ""
    duration_ms: int = 0
    sources: dict[str, SourceReport] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
            "sources": {k: vars(v) for k, v in self.sources.items()},
        }


# --------------------------------------------------------------------------- readers


RowFn = Callable[[TableSpec, str, dict[str, Any]], dict[str, Any] | None]


async def _scan(
    collection: str, field_name: str, since: datetime, limit: int
) -> list[tuple[str, dict[str, Any]]]:
    """``collection where field >= since order by field`` in pages of ``PAGE_SIZE`` (single-field index)."""
    col = get_db().collection(collection)
    out: list[tuple[str, dict[str, Any]]] = []
    last = None
    while len(out) < limit:
        q = (
            col.where(filter=FieldFilter(field_name, ">=", since))
            .order_by(field_name)
            .limit(min(PAGE_SIZE, limit - len(out)))
        )
        if last is not None:
            q = q.start_after(last)
        snaps = [s async for s in q.stream()]
        if not snaps:
            break
        out.extend((s.id, s.to_dict() or {}) for s in snaps)
        last = snaps[-1]
        if len(snaps) < PAGE_SIZE:
            break
    return out


def _max_ts(docs: list[tuple[str, dict[str, Any]]], field_name: str) -> datetime | None:
    values = [d.get(field_name) for _, d in docs if isinstance(d.get(field_name), datetime)]
    return max(values) if values else None


async def _model_messages(conversation_id: str) -> list[tuple[str, dict[str, Any]]]:
    col = get_db().collection(Col.CONVERSATIONS).document(conversation_id).collection(Col.MESSAGES)
    q = col.where(filter=FieldFilter("role", "==", "model"))
    return [(s.id, s.to_dict() or {}) async for s in q.stream()]


# --------------------------------------------------------------------------- the job


class SyncJob:
    def __init__(self, warehouse: Warehouse, state: SyncState) -> None:
        self.warehouse = warehouse
        self.state = state

    async def _since(self, key: str, full: bool, lookback: timedelta) -> datetime:
        if full:
            return EPOCH
        wm = await self.state.get_watermark(key)
        return (wm - lookback) if wm else EPOCH

    async def _simple(
        self, key: str, spec: TableSpec, collection: str, field_name: str, fn: RowFn, *, full: bool,
        lookback: timedelta = OVERLAP,
    ) -> SourceReport:  # fmt: skip
        since = await self._since(key, full, lookback)
        docs = await _scan(collection, field_name, since, MAX_ROWS_PER_SOURCE)
        rows = [r for doc_id, d in docs if (r := fn(spec, doc_id, d)) is not None]
        written = await self.warehouse.merge_rows(spec, rows) if rows else 0
        report = SourceReport(read=len(docs), written=written)
        wm = _max_ts(docs, field_name)
        if wm is not None:
            await self.state.set_watermark(key, wm, len(rows))
            report.watermark = transform.ts(wm)
        return report

    async def _conversations(self, *, full: bool) -> tuple[SourceReport, SourceReport]:
        key = "conversations"
        since = await self._since(key, full, OVERLAP)
        docs = await _scan(Col.CONVERSATIONS, "updated_at", since, MAX_ROWS_PER_SOURCE)
        conv_rows = [r for doc_id, d in docs if (r := transform.conversation_row(CONVERSATIONS, doc_id, d))]
        sem = asyncio.Semaphore(MESSAGE_READ_CONCURRENCY)

        async def rounds_of(conv_id: str, conv: dict[str, Any]) -> list[dict[str, Any]]:
            if not conv.get("owner_uid"):
                return []
            async with sem:
                messages = await _model_messages(conv_id)
            rows = (transform.round_row(ROUNDS, conv_id, conv, mid, m) for mid, m in messages)
            return [r for r in rows if r is not None]

        round_lists = await asyncio.gather(*(rounds_of(cid, d) for cid, d in docs))
        round_rows = [r for rows in round_lists for r in rows]
        # Rounds first: if the conversations merge fails, the watermark does not move and both re-run.
        rounds_written = await self.warehouse.merge_rows(ROUNDS, round_rows) if round_rows else 0
        conv_written = await self.warehouse.merge_rows(CONVERSATIONS, conv_rows) if conv_rows else 0
        conv_report = SourceReport(read=len(docs), written=conv_written)
        rounds_report = SourceReport(read=len(round_rows), written=rounds_written)
        wm = _max_ts(docs, "updated_at")
        if wm is not None:
            await self.state.set_watermark(key, wm, len(conv_rows))
            conv_report.watermark = rounds_report.watermark = transform.ts(wm)
        return conv_report, rounds_report

    async def run(self, *, full: bool = False) -> SyncReport:
        """One sync of every source. Holds the lease for the whole run; never runs twice concurrently."""
        owner = secrets.token_hex(8)
        if not await self.state.try_lease(owner, LEASE_TTL):
            return SyncReport(ran=False, reason="busy")
        started = time.monotonic()
        report = SyncReport(ran=True)
        finished = False
        try:
            await self.warehouse.ensure_schema()
            report.sources["ledger"] = await self._simple(
                "ledger", LEDGER, Col.LEDGER, "created_at", transform.ledger_row, full=full
            )
            report.sources["conversations"], report.sources["rounds"] = await self._conversations(full=full)
            report.sources["a2a_calls"] = await self._simple(
                "a2a_calls", A2A_CALLS, Col.A2A_CALLS, "created_at", transform.a2a_call_row, full=full,
                lookback=A2A_LOOKBACK,
            )  # fmt: skip
            report.sources["records"] = await self._simple(
                "records", RECORDS, Col.RECORDS, "updated_at", transform.record_row, full=full
            )
            report.sources["agents"] = await self._simple(
                "agents", AGENTS, Col.AGENTS, "updated_at", transform.agent_row, full=full
            )
            finished = True
        finally:
            report.duration_ms = int((time.monotonic() - started) * 1000)
            await self.state.release_lease(owner, finished=finished)
            log.info(
                "analytics_sync finished=%s duration_ms=%d sources=%s",
                finished,
                report.duration_ms,
                {k: (v.read, v.written) for k, v in report.sources.items()},
            )
        return report


# --------------------------------------------------------------------------- throttled entry points

_run_lock = asyncio.Lock()
_current: asyncio.Task | None = None


async def sync_if_stale(
    job_factory: Callable[[], SyncJob],
    *,
    min_interval_s: float,
    wait_s: float,
    spawn: Callable[[Awaitable[Any]], asyncio.Task],
) -> SyncReport:
    """On-demand sync for a question: skip when fresh, else start (or join) a run and wait ≤ ``wait_s``.

    The run continues in the background when the wait times out; the caller answers from the data that is
    already in the warehouse (fail-open: stale data beats no answer).
    """
    global _current
    async with _run_lock:
        if _current is None or _current.done():
            job = job_factory()
            last = await job.state.last_finished_at()
            if last is not None and (utcnow() - last).total_seconds() < min_interval_s:
                return SyncReport(ran=False, reason="fresh")
            _current = spawn(job.run())
        task = _current
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=wait_s)
    except TimeoutError:
        return SyncReport(ran=False, reason="still_running")
