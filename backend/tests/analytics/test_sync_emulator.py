"""Incremental Firestore → warehouse sync on the emulator with a fake BigQuery (SPEC §18 analytics)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from topskill.analytics import sync as sync_module
from topskill.analytics.sync import FirestoreSyncState, MemorySyncState, SyncJob, sync_if_stale
from topskill.db import Col, get_db, utcnow
from topskill.records import service as records_service
from topskill.testing.factories import unique

from .fakes import FakeWarehouse

pytestmark = pytest.mark.emulator


async def seed(owner: str, *, ts_offset_s: int = 0) -> dict[str, Any]:
    """One document of every source for ``owner``; returns their ids."""
    db = get_db()
    now = utcnow() - timedelta(seconds=ts_offset_s)
    agent_id = f"agt_{unique()}"
    cid = f"cnv_{unique()}"
    call_id = unique("call")
    ledger_id = f"{owner}__income_turn_{unique()}"
    record_id = f"{agent_id}__{cid}"
    batch = db.batch()
    batch.set(
        db.collection(Col.AGENTS).document(agent_id),
        {
            "id": agent_id,
            "owner_uid": owner,
            "slug": f"s-{agent_id}",
            "title": "Visa Advisor",
            # Not a full AgentDoc: keep it out of every marketplace/directory query of other tests.
            "status": "paused",
            "listed": False,
            "deleted": True,
            "pricing": {"mode": "per_round", "price_cents": 100, "a2a_price_cents": 200, "a2a_enabled": True},
            "config": {"model": "gemini-3.8-flash"},
            "created_at": now,
            "updated_at": now,
        },
    )
    batch.set(
        db.collection(Col.LEDGER).document(ledger_id),
        {
            "uid": owner,
            "request_id": "income_turn_x",
            "kind": "CREDIT",
            "category": "creator_income",
            "amount_cents": 80,
            "agent_id": agent_id,
            "agent_title": "Visa Advisor",
            "created_at": now,
        },
    )
    conv_ref = db.collection(Col.CONVERSATIONS).document(cid)
    batch.set(
        conv_ref,
        {
            "id": cid,
            "agent_id": agent_id,
            "agent_slug": f"s-{agent_id}",
            "owner_uid": owner,
            "visitor_uid": "vis_1",
            "channel": "web",
            "title": "Work visa?",
            "rounds_ok": 1,
            "message_count": 2,
            "created_at": now,
            "updated_at": now,
        },
    )
    batch.set(
        conv_ref.collection(Col.MESSAGES).document("u_turn_1"),
        {"id": "u_turn_1", "role": "user", "text": "Work visa?", "status": "ok", "created_at": now},
    )
    batch.set(
        conv_ref.collection(Col.MESSAGES).document("m_turn_1"),
        {
            "id": "m_turn_1",
            "role": "model",
            "text": "Here is the plan",
            "status": "ok",
            "charge": {"charged_cents": 100, "free": False, "round_index": 3},
            "usage": {"model": "gemini-3.8-flash"},
            "created_at": now,
        },
    )
    batch.set(
        db.collection(Col.A2A_CALLS).document(call_id),
        {
            "id": call_id,
            "owner_uid": owner,
            "agent_id": agent_id,
            "agent_title": "Visa Advisor",
            "payer_uid": "payer_1",
            "status": "running",
            "price_cents": 200,
            "charged_cents": 0,
            "created_at": now,
            "finished_at": None,
        },
    )
    batch.set(
        db.collection(Col.RECORDS).document(record_id),
        {
            "id": record_id,
            "agent_id": agent_id,
            "owner_uid": owner,
            "conversation_id": cid,
            "status": "in_progress",
            "follow_status": "new",
            "data": {"nationality": {"value": "br"}},
            "created_at": now,
            "updated_at": now,
        },
    )
    await batch.commit()
    return {"agent": agent_id, "cid": cid, "call": call_id, "ledger": ledger_id, "record": record_id}


def counts(wh: FakeWarehouse, owner: str) -> dict[str, int]:
    return {
        t: len(wh.rows_for(t, owner))
        for t in ("fs_ledger", "fs_conversations", "fs_rounds", "fs_a2a_calls", "fs_records", "fs_agents")
    }


async def test_sync_is_idempotent_and_incremental() -> None:
    owner = unique("own_")
    ids = await seed(owner)
    wh = FakeWarehouse()
    state = FirestoreSyncState(prefix=f"{unique('test_')}_")
    job = SyncJob(wh, state)

    first = await job.run()
    assert first.ran
    assert counts(wh, owner) == {
        "fs_ledger": 1,
        "fs_conversations": 1,
        "fs_rounds": 1,
        "fs_a2a_calls": 1,
        "fs_records": 1,
        "fs_agents": 1,
    }
    snapshot = {t: dict(rows) for t, rows in wh.tables.items()}
    rnd = wh.rows_for("fs_rounds", owner)[f"{ids['cid']}__m_turn_1"]
    assert rnd["charged_cents"] == 100 and rnd["agent_id"] == ids["agent"]

    # Re-running with nothing new changes nothing (MERGE by id: same rows, no duplicates).
    second = await job.run()
    assert second.ran
    assert wh.tables == snapshot

    # A changed document and a new one are picked up incrementally; the old rows are updated in place.
    db = get_db()
    later = utcnow() + timedelta(seconds=1)
    await (
        db.collection(Col.A2A_CALLS)
        .document(ids["call"])
        .update(
            {"status": "completed", "charged_cents": 200, "creator_share_cents": 160, "finished_at": later}
        )
    )
    await (
        db.collection(Col.RECORDS)
        .document(ids["record"])
        .update({"status": "completed", "updated_at": later, "data.budget": {"value": 5000}})
    )
    new_ledger = f"{owner}__a2a_income_{unique()}"
    await (
        db.collection(Col.LEDGER)
        .document(new_ledger)
        .set({"uid": owner, "category": "a2a_income", "amount_cents": 160, "created_at": later})
    )
    third = await job.run()
    assert third.ran
    c = counts(wh, owner)
    assert c["fs_ledger"] == 2 and c["fs_a2a_calls"] == 1 and c["fs_records"] == 1
    call = wh.rows_for("fs_a2a_calls", owner)[ids["call"]]
    assert (call["status"], call["charged_cents"], call["creator_share_cents"]) == ("completed", 200, 160)
    record = wh.rows_for("fs_records", owner)[ids["record"]]
    assert record["status"] == "completed" and record["filled_keys"] == ["budget", "nationality"]


async def test_late_commit_inside_the_overlap_window_is_not_lost() -> None:
    owner = unique("own_")
    wh = FakeWarehouse()
    job = SyncJob(wh, FirestoreSyncState(prefix=f"{unique('test_')}_"))
    await seed(owner)
    await job.run()
    # A document whose timestamp is slightly older than the watermark, committed after the run
    # (e.g. a settlement transaction that retried) is still copied by the next run.
    await seed(owner, ts_offset_s=30)
    await job.run()
    assert counts(wh, owner)["fs_ledger"] == 2
    assert counts(wh, owner)["fs_rounds"] == 2


async def test_full_resync_rewrites_the_same_rows() -> None:
    owner = unique("own_")
    wh = FakeWarehouse()
    job = SyncJob(wh, FirestoreSyncState(prefix=f"{unique('test_')}_"))
    await seed(owner)
    await job.run()
    before = counts(wh, owner)
    report = await job.run(full=True)
    assert report.sources["ledger"].read >= 1
    assert counts(wh, owner) == before


async def test_lease_prevents_concurrent_runs_and_failed_runs_keep_the_watermark() -> None:
    owner = unique("own_")
    await seed(owner)
    state = FirestoreSyncState(prefix=f"{unique('test_')}_")
    assert await state.try_lease("other-worker", timedelta(minutes=5))
    busy = await SyncJob(FakeWarehouse(), state).run()
    assert not busy.ran and busy.reason == "busy"
    await state.release_lease("other-worker", finished=False)

    failing = FakeWarehouse(fail_merge=True)
    with pytest.raises(Exception, match="merge failed"):
        await SyncJob(failing, state).run()
    assert await state.get_watermark("ledger") is None  # nothing advanced
    assert await state.last_finished_at() is None
    ok = await SyncJob(FakeWarehouse(), state).run()  # lease was released by the failed run
    assert ok.ran and await state.last_finished_at() is not None


async def test_memory_state_lease_semantics() -> None:
    state = MemorySyncState()
    assert await state.try_lease("a", timedelta(minutes=1))
    assert not await state.try_lease("b", timedelta(minutes=1))
    await state.release_lease("a", finished=True)
    assert await state.try_lease("b", timedelta(minutes=1))
    assert await state.last_finished_at() is not None


def merged_for(wh: FakeWarehouse, owner: str) -> list[tuple[str, str]]:
    return [(table, row["id"]) for table, row in wh.merged if row["owner_uid"] == owner]


async def test_unchanged_rows_in_the_overlap_window_are_not_merged_again() -> None:
    owner = unique("own_")
    ids = await seed(owner)
    wh = FakeWarehouse()
    job = SyncJob(wh, FirestoreSyncState(prefix=f"{unique('test_')}_"))
    await job.run()
    assert len(merged_for(wh, owner)) == 6
    wh.merged.clear()

    second = await job.run()  # every doc of this owner is re-read (overlap window) but none changed
    assert second.ran and merged_for(wh, owner) == []

    # A change inside the A2A lookback (no timestamp moves) is still detected and merged, alone.
    await get_db().collection(Col.A2A_CALLS).document(ids["call"]).update({"status": "failed"})
    await job.run()
    assert merged_for(wh, owner) == [("fs_a2a_calls", ids["call"])]
    assert wh.rows_for("fs_a2a_calls", owner)[ids["call"]]["status"] == "failed"

    # A full resync ignores the fingerprints.
    wh.merged.clear()
    await job.run(full=True)
    assert len(merged_for(wh, owner)) == 6


async def test_follow_up_changes_reach_the_warehouse() -> None:
    owner = unique("own_")
    ids = await seed(owner)
    wh = FakeWarehouse()
    job = SyncJob(wh, FirestoreSyncState(prefix=f"{unique('test_')}_"))
    await job.run()
    assert wh.rows_for("fs_records", owner)[ids["record"]]["follow_status"] == "new"

    # P1 follow-up endpoints change follow_status/followed_at only (updated_at does not move).
    agent = SimpleNamespace(id=ids["agent"])
    await records_service.update_follow(agent, ids["record"], follow_status="done", follow_note=None)  # type: ignore[arg-type]
    report = await job.run()
    row = wh.rows_for("fs_records", owner)[ids["record"]]
    assert row["follow_status"] == "done" and row["followed_at"] is not None
    assert report.sources["records_follow"].read >= 1

    await records_service.batch_follow(agent, [ids["record"]], "invalid")  # type: ignore[arg-type]
    await job.run()
    assert wh.rows_for("fs_records", owner)[ids["record"]]["follow_status"] == "invalid"


async def test_failing_sync_is_attempted_once_per_interval(monkeypatch) -> None:
    monkeypatch.setattr(sync_module, "_current", None)
    state = MemorySyncState()
    runs = 0

    class FailingJob(SyncJob):
        async def run(self, *, full: bool = False):  # type: ignore[override]
            nonlocal runs
            runs += 1
            assert await self.state.try_lease("x", timedelta(minutes=1))
            await self.state.release_lease("x", finished=False)
            raise RuntimeError("bigquery down")

    def factory() -> SyncJob:
        return FailingJob(FakeWarehouse(), state)

    async def call():
        return await sync_if_stale(factory, min_interval_s=60, wait_s=1, spawn=asyncio.ensure_future)

    with pytest.raises(RuntimeError, match="bigquery down"):
        await call()
    again = await call()
    assert runs == 1 and not again.ran and again.reason == "recent_attempt"
    assert await state.last_finished_at() is None  # data_as_of still reflects the last success

    state.attempted_at = utcnow() - timedelta(seconds=61)
    with pytest.raises(RuntimeError):
        await call()
    assert runs == 2
