from __future__ import annotations

import asyncio
import json

import pytest

from topskill.events import DoneData, StatusData, TextDeltaData
from topskill.sse import sse_event, sse_response

pytestmark = pytest.mark.unit


def parse_frames(raw: bytes) -> list[tuple[str, dict]]:
    frames = []
    for block in raw.decode().split("\n\n"):
        if not block or block.startswith(":"):
            continue
        lines = block.split("\n")
        assert lines[0].startswith("event: ") and lines[1].startswith("data: ") and len(lines) == 2
        frames.append((lines[0][7:], json.loads(lines[1][6:])))
    return frames


def test_frame_format() -> None:
    frame = sse_event("text_delta", TextDeltaData(delta="line1\nline2 é"))
    assert frame == 'event: text_delta\ndata: {"delta":"line1\\nline2 é"}\n\n'.encode()
    assert sse_event("x", {"a": None}) == b'event: x\ndata: {"a":null}\n\n'
    with pytest.raises(ValueError):
        sse_event("bad\nname", {})


def test_models_serialize_nulls() -> None:
    frame = sse_event(
        "done",
        DoneData(
            conversation_id=None, message_id=None, turn_id="t1", outcome="failed", usage=None, billing=None
        ),
    )
    assert parse_frames(frame) == [
        (
            "done",
            {
                "conversation_id": None,
                "message_id": None,
                "turn_id": "t1",
                "outcome": "failed",
                "usage": None,
                "billing": None,
            },
        )
    ]


async def collect(resp) -> bytes:
    out = b""
    async for chunk in resp.body_iterator:
        out += chunk if isinstance(chunk, bytes) else chunk.encode()
    return out


async def test_response_headers_and_order() -> None:
    async def events():
        yield "status", StatusData(conversation_id=None, turn_id="t", request_id="turn_x", model="m")
        yield "text_delta", {"delta": "hi"}

    resp = sse_response(events())
    assert resp.media_type == "text/event-stream"
    assert resp.headers["cache-control"] == "no-cache, no-transform"
    assert resp.headers["x-accel-buffering"] == "no"
    frames = parse_frames(await collect(resp))
    assert [f[0] for f in frames] == ["status", "text_delta"]
    assert frames[0][1]["state"] == "ready"


async def test_heartbeat_on_silence() -> None:
    async def slow():
        await asyncio.sleep(0.25)
        yield "text_delta", {"delta": "late"}

    raw = await collect(sse_response(slow(), heartbeat_s=0.05))
    assert raw.count(b": ping\n\n") >= 2
    assert parse_frames(raw) == [("text_delta", {"delta": "late"})]


async def test_close_cancels_source() -> None:
    closed = asyncio.Event()

    async def endless():
        try:
            while True:
                await asyncio.sleep(10)
                yield "x", {}
        finally:
            closed.set()

    resp = sse_response(endless(), heartbeat_s=0.01)
    it = resp.body_iterator
    assert await it.__anext__() == b": ping\n\n"
    await it.aclose()
    await asyncio.wait_for(closed.wait(), 1)
