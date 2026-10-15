"""Voice call (SPEC §18.1) with tracing on (SPEC §18.3): the WebSocket still works through the OTel
middleware, and it is one server span named after its route template (no per-frame spans)."""

from __future__ import annotations

import asyncio

import pytest

from tests.voice.conftest import voice_env  # noqa: F401 (shared voice fixture)
from tests.voice.fakes import AsgiWebSocket, answer, heard
from topskill.observability.tracing import instrument_app
from topskill.testing.factories import make_agent, make_user, open_wallet

pytestmark = pytest.mark.emulator

PCM_FRAME = b"\x00\x01" * 800  # 50 ms of 16 kHz PCM16


async def test_voice_call_is_one_named_server_span(otel, voice_env) -> None:  # noqa: F811
    from topskill.main import create_app

    app = create_app()
    instrument_app(app, otel.provider)
    owner = await make_user("creator")
    visitor = await make_user()
    agent = await make_agent(owner, pricing={"free_rounds": 1, "price_cents": 100})
    await open_wallet(visitor.uid, 1_000)

    ws = await AsgiWebSocket(app, f"/api/public/agents/{agent.slug}/voice").connect()
    assert ws.accepted
    await ws.send_json({"type": "start", "dev": {"uid": visitor.uid}, "conversation_id": None})
    await ws.next_event("ready")
    live = await voice_env.connector.session()
    for _ in range(5):
        await ws.send_bytes(PCM_FRAME)
    await asyncio.wait_for(live.got_audio.wait(), 5)
    live.push(heard("Hello?"), *answer("Hi, how can I help?"))
    rnd = await ws.next_event("round")
    assert rnd["outcome"] == "ok"
    await ws.send_json({"type": "stop"})
    await ws.next_event("closed")
    await ws.drain()
    assert ws.close_code == 1000
    await ws.finish()

    names = [s.name for s in otel.spans()]
    server = otel.one("WS /api/public/agents/{slug}/voice")
    assert dict(server.attributes or {})["http.route"] == "/api/public/agents/{slug}/voice"
    # send/receive spans are excluded: 5 audio frames + JSON events do not become spans.
    assert not [n for n in names if "send" in n or "receive" in n], names
