"""Server-Sent Events transport (SPEC §7.1). ``sse_response`` is the only way to stream."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator

from pydantic import BaseModel
from starlette.responses import StreamingResponse

log = logging.getLogger(__name__)

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
PING_FRAME = b": ping\n\n"


def sse_event(event: str, data: BaseModel | dict) -> bytes:
    """One frame: ``event: <name>\\ndata: <compact JSON>\\n\\n`` (JSON never contains raw newlines)."""
    if "\n" in event or "\r" in event or not event:
        raise ValueError("invalid SSE event name")
    payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {body}\n\n".encode()


async def _framed(
    events: AsyncIterator[tuple[str, BaseModel | dict]], heartbeat_s: float
) -> AsyncIterator[bytes]:
    iterator = events.__aiter__()
    pending: asyncio.Task | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=heartbeat_s)
            if not done:
                yield PING_FRAME
                continue
            task, pending = pending, None
            try:
                name, data = task.result()
            except StopAsyncIteration:
                return
            yield sse_event(name, data)
    finally:
        # Client went away (generator closed) or the source finished: stop the in-flight __anext__
        # before closing the source, otherwise aclose() would hit "generator already running".
        if pending is not None and not pending.done():
            pending.cancel()
            with contextlib.suppress(BaseException):
                await pending
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()


def sse_response(
    events: AsyncIterator[tuple[str, BaseModel | dict]], *, heartbeat_s: float = 15.0
) -> StreamingResponse:
    """Stream ``(event_name, payload)`` pairs as SSE with a ``: ping`` comment after each
    ``heartbeat_s`` of silence."""
    return StreamingResponse(
        _framed(events, heartbeat_s), media_type="text/event-stream", headers=dict(SSE_HEADERS)
    )
