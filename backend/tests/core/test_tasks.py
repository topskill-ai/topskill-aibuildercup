from __future__ import annotations

import asyncio
import gc
import logging

import pytest

from topskill import tasks
from topskill.errors import reset_trace_id, set_trace_id

pytestmark = pytest.mark.unit


async def test_spawn_keeps_reference_and_runs() -> None:
    done = asyncio.Event()

    async def work() -> None:
        await asyncio.sleep(0.01)
        done.set()

    task = tasks.spawn(work(), name="t:keep")
    assert task in tasks.pending() and task.get_name() == "t:keep"
    del task
    gc.collect()
    await asyncio.wait_for(done.wait(), 1)
    await asyncio.sleep(0)
    assert not any(t.get_name() == "t:keep" for t in tasks.pending())


async def test_spawn_logs_exception_with_trace_id(caplog: pytest.LogCaptureFixture) -> None:
    seen: list[str] = []

    async def fail() -> None:
        from topskill.errors import current_trace_id

        seen.append(current_trace_id())
        raise ValueError("boom")

    token = set_trace_id("abc123trace")
    try:
        task = tasks.spawn(fail(), name="t:fail")
    finally:
        reset_trace_id(token)
    with caplog.at_level(logging.ERROR, logger="topskill.tasks"):
        with pytest.raises(ValueError):
            await task
        await asyncio.sleep(0)
    assert seen == ["abc123trace"]
    records = [r for r in caplog.records if "background_task_failed" in r.getMessage()]
    assert records and records[0].trace_id == "abc123trace" and records[0].exc_info


async def test_drain_waits_with_timeout() -> None:
    finished = asyncio.Event()

    async def short() -> None:
        await asyncio.sleep(0.05)
        finished.set()

    async def long() -> None:
        await asyncio.sleep(5)

    tasks.spawn(short(), name="t:short")
    slow = tasks.spawn(long(), name="t:long")
    await tasks.drain(timeout=0.5)
    assert finished.is_set() and not slow.done()
    slow.cancel()
