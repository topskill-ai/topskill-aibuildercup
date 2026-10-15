"""Background tasks (SPEC §3.1, §14.2).

Background work is started only via :func:`spawn`, never a bare ``asyncio.create_task``: the task is
kept alive by a strong reference, runs with the trace id of the request that started it, and any
exception is logged with that trace id. :func:`drain` lets the lifespan shutdown wait for pending work.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Coroutine
from typing import Any

from topskill.errors import current_trace_id, set_trace_id

log = logging.getLogger(__name__)

_tasks: set[asyncio.Task] = set()


def _on_done(task: asyncio.Task, trace_id: str) -> None:
    _tasks.discard(task)
    if task.cancelled():
        log.warning("background_task_cancelled name=%s", task.get_name(), extra={"trace_id": trace_id or "-"})
        return
    exc = task.exception()
    if exc is not None:
        log.error(
            "background_task_failed name=%s error=%s",
            task.get_name(),
            type(exc).__name__,
            exc_info=exc,
            extra={"trace_id": trace_id or "-"},
        )


def spawn(coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task:
    """Start ``coro`` as a tracked background task named ``name``."""
    trace_id = current_trace_id()
    ctx = contextvars.copy_context()
    ctx.run(set_trace_id, trace_id or None)
    task = asyncio.get_running_loop().create_task(coro, name=name, context=ctx)
    _tasks.add(task)
    task.add_done_callback(lambda t: _on_done(t, trace_id))
    return task


def pending() -> set[asyncio.Task]:
    """A snapshot of the tasks still running."""
    return {t for t in _tasks if not t.done()}


async def drain(timeout: float) -> None:  # noqa: ASYNC109 (SPEC §14.2 signature)
    """Wait up to ``timeout`` seconds for pending tasks (Cloud Run sends SIGTERM ~10 s before kill)."""
    tasks = pending()
    if not tasks:
        return
    log.info("draining_background_tasks count=%d timeout=%.1fs", len(tasks), timeout)
    _, still_running = await asyncio.wait(tasks, timeout=timeout)
    for task in still_running:
        log.warning("background_task_unfinished_at_shutdown name=%s", task.get_name())
