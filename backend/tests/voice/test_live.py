"""Real Gemini Live (Vertex AI) through the voice WebSocket, on the Firestore emulator.

Run manually: ``../deploy/scripts/emu_exec.sh … -- uv run --with 'python-socks[asyncio]' pytest -m live
tests/voice/test_live.py`` with ``WSS_PROXY=socks5h://…`` when a local HTTP proxy blocks WebSockets
(macOS only: the question is synthesised with ``say``).
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from scripts.voice_e2e import spoken_pcm
from tests.billing.helpers import balance
from tests.voice.fakes import AsgiWebSocket
from topskill import billing
from topskill.chat import deps as chat_deps
from topskill.db import Col, get_db
from topskill.domain import MessageDoc
from topskill.testing.factories import make_agent, make_user, open_wallet
from topskill.voice import live

pytestmark = [
    pytest.mark.live,
    pytest.mark.emulator,
    pytest.mark.skipif(not shutil.which("say"), reason="needs macOS say/afconvert"),
]

CHUNK = 1280  # 40 ms of 16 kHz PCM16


@pytest.fixture
def real_live():
    live.set_connector(None)
    chat_deps.set_deps(None)
    yield
    chat_deps.set_deps(None)


async def test_spoken_question_gets_a_spoken_answer_and_a_settled_round(app, real_live) -> None:
    owner = await make_user("creator")
    visitor = await make_user()
    agent = await make_agent(
        owner,
        title="Visa Advisor",
        pricing={"free_rounds": 0, "price_cents": 100},
        config={"soul": "You are a friendly visa advisor. Answer in one or two short sentences."},
    )
    await open_wallet(visitor.uid, 1_000)
    pcm = await asyncio.to_thread(spoken_pcm, "Hello! What is a work visa? Answer in one sentence.")

    ws = await AsgiWebSocket(app, f"/api/public/agents/{agent.slug}/voice").connect()
    await ws.send_json({"type": "start", "dev": {"uid": visitor.uid}})
    ready = await ws.next_event("ready", timeout=30)
    assert ready["model"] == live.live_plans()[0][0]

    for i in range(0, len(pcm), CHUNK):
        await ws.send_bytes(pcm[i : i + CHUNK])
        await asyncio.sleep(0.04)

    user_caption, model_caption, audio_bytes = "", "", 0
    rnd: dict | None = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 60
    while rnd is None and loop.time() < deadline:
        try:
            item = await ws.receive(timeout=0.04)
        except TimeoutError:
            await ws.send_bytes(b"\x00" * CHUNK)  # trailing silence for voice activity detection
            continue
        if isinstance(item, bytes):
            audio_bytes += len(item)
        elif item["type"] == "transcript":
            if item["role"] == "user":
                user_caption += item["text"]
            else:
                model_caption += item["text"]
        elif item["type"] == "round" and item["outcome"] == "ok":
            rnd = item
        elif item["type"] == "error":
            pytest.fail(f"voice error: {item}")
    await ws.send_json({"type": "stop"})
    await ws.drain(timeout=30)
    await ws.finish()

    assert "visa" in user_caption.lower(), user_caption
    assert model_caption.strip()
    assert audio_bytes > 24_000  # more than half a second of 24 kHz PCM16
    assert rnd is not None and rnd["billing"]["charged_cents"] == 100
    col = get_db().collection(Col.CONVERSATIONS).document(ready["conversation_id"]).collection(Col.MESSAGES)
    msgs = [MessageDoc.model_validate(s.to_dict()) async for s in col.stream()]
    assert {m.role for m in msgs} == {"user", "model"}
    assert await balance(visitor.uid) == 900
    assert billing.is_stub is False


async def stream_speech(ws: AsgiWebSocket, pcm: bytes) -> None:
    for i in range(0, len(pcm), CHUNK):
        await ws.send_bytes(pcm[i : i + CHUNK])
        await asyncio.sleep(0.04)


async def test_speech_after_the_answer_was_generated_still_interrupts(app, real_live) -> None:
    """The model generates faster than the browser plays: Live must still send ``interrupted`` when the
    visitor speaks after ``turn_complete`` (verified 2026-10-15 for both live models), so playback stops."""
    owner = await make_user("creator")
    visitor = await make_user()
    agent = await make_agent(
        owner, title="Storyteller", config={"soul": "You tell long stories, about a minute of speech."}
    )
    story = await asyncio.to_thread(spoken_pcm, "Tell me a long story about a dragon and a knight.")
    stop = await asyncio.to_thread(spoken_pcm, "Stop. What is two plus two?")

    ws = await AsgiWebSocket(app, f"/api/public/agents/{agent.slug}/voice").connect()
    await ws.send_json({"type": "start", "dev": {"uid": visitor.uid}})
    await ws.next_event("ready", timeout=30)
    await stream_speech(ws, story)

    first: dict | None = None
    interrupted_turn: str | None = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 90
    speaker: asyncio.Task | None = None
    while interrupted_turn is None and loop.time() < deadline:
        try:
            item = await ws.receive(timeout=0.04)
        except TimeoutError:
            if speaker is None:
                await ws.send_bytes(b"\x00" * CHUNK)
            continue
        if not isinstance(item, dict):
            continue
        if item["type"] == "round" and first is None:
            first = item  # generation of the story is complete; the browser would still be playing it
            speaker = asyncio.create_task(stream_speech(ws, stop))
        elif item["type"] == "interrupted" and first is not None:
            interrupted_turn = item["turn_id"]
        elif item["type"] == "error":
            pytest.fail(f"voice error: {item}")
    if speaker is not None:
        await speaker
    await ws.send_json({"type": "stop"})
    await ws.drain(timeout=30)
    await ws.finish()

    assert first is not None and first["outcome"] == "ok"
    assert interrupted_turn is not None and interrupted_turn != first["turn_id"]
