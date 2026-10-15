"""Voice sessions end to end with a scripted Gemini Live fake, real billing, on the Firestore emulator."""

from __future__ import annotations

import asyncio

import pytest

from tests.billing.helpers import balance, ledger
from tests.conftest import dev_headers
from tests.core.helpers import settings_env
from tests.voice.fakes import (
    AsgiWebSocket,
    answer,
    audio,
    go_away,
    heard,
    interrupted,
    said,
    tool_call,
    turn_complete,
    web_grounding,
)
from topskill.chat import locks
from topskill.db import Col, get_db
from topskill.domain import MessageDoc
from topskill.testing.factories import make_agent, make_conversation, make_user, open_wallet
from topskill.voice import registry
from topskill.voice.session import VoiceSession

pytestmark = pytest.mark.emulator

PCM_FRAME = b"\x00\x01" * 800  # 50 ms of 16 kHz PCM16


async def setup_agent(**overrides):
    owner = await make_user("creator")
    visitor = await make_user()
    agent = await make_agent(owner, **overrides)
    return owner, visitor, agent


async def start_call(app, agent, user, *, conversation_id=None, headers=None) -> AsgiWebSocket:
    ws = await AsgiWebSocket(app, f"/api/public/agents/{agent.slug}/voice", headers).connect()
    assert ws.accepted
    await ws.send_json({"type": "start", "dev": {"uid": user.uid}, "conversation_id": conversation_id})
    return ws


async def messages_of(cid: str) -> dict[str, MessageDoc]:
    col = get_db().collection(Col.CONVERSATIONS).document(cid).collection(Col.MESSAGES)
    return {s.id: MessageDoc.model_validate(s.to_dict()) async for s in col.stream()}


async def conversation(cid: str) -> dict | None:
    snap = await get_db().collection(Col.CONVERSATIONS).document(cid).get()
    return snap.to_dict() if snap.exists else None


# --------------------------------------------------------------------------- happy path


async def test_spoken_round_with_knowledge_web_and_billing(app, client, voice_env) -> None:
    _, visitor, agent = await setup_agent(pricing={"free_rounds": 1, "price_cents": 100})
    await open_wallet(visitor.uid, 1_000)
    ws = await start_call(app, agent, visitor)
    ready = await ws.next_event("ready")
    assert ready["model"] == "gemini-live-2.5-flash"
    assert ready["input_sample_rate"] == 16000 and ready["output_sample_rate"] == 24000
    assert ready["next_round_charge_cents"] == 0  # first round is free
    cid = ready["conversation_id"]

    live = await voice_env.connector.session()
    config = live.config
    assert "## Voice call" in config.system_instruction.parts[0].text
    assert [d.name for d in config.tools[0].function_declarations] == ["search_knowledge"]
    assert config.tools[-1].google_search is not None
    assert config.input_audio_transcription is not None and config.output_audio_transcription is not None

    await ws.send_bytes(PCM_FRAME)
    await asyncio.wait_for(live.got_audio.wait(), 5)
    assert bytes(live.audio) == PCM_FRAME

    # Round 1 (free): caption, a knowledge search, a web search, the spoken answer.
    live.push(
        heard("What is the"), heard(" work visa fee?"), tool_call("search_knowledge", {"query": "visa fee"})
    )
    tool_running = await ws.next_event("tool")
    assert tool_running["tool"]["status"] == "running" and tool_running["tool"]["kind"] == "search"
    await asyncio.wait_for(live.got_tool_response.wait(), 5)
    response = live.tool_responses[0][0]
    assert response.name == "search_knowledge" and response.response["results"][0]["ref"] == "[1]"
    live.push(
        turn_complete(),  # the tool-call turn ends without an answer: not the end of the round
        web_grounding("visa fee 2026", "https://gov.example/visa", "gov.example"),
        *answer("The fee is 290 USD [1]."),
    )
    rnd = await ws.next_event("round")
    assert rnd["outcome"] == "ok" and rnd["conversation_id"] == cid
    assert rnd["billing"]["free"] is True and rnd["billing"]["round_index"] == 1
    assert [c["kind"] for c in rnd["citations"]] == ["knowledge", "web"]

    # Round 2 (charged).
    live.push(heard("Thanks, and how long does it take?"), *answer("About three weeks."))
    rnd2 = await ws.next_event("round")
    assert rnd2["outcome"] == "ok"
    assert rnd2["billing"]["charged_cents"] == 100 and rnd2["billing"]["balance_cents"] == 900

    await ws.send_json({"type": "stop"})
    closed = await ws.next_event("closed")
    assert closed == {
        "type": "closed",
        "reason": "client",
        "conversation_id": cid,
        "rounds": 2,
        "duration_s": 0,
    }
    await ws.drain()
    assert ws.close_code == 1000
    await ws.finish()

    msgs = await messages_of(cid)
    models = sorted((m for m in msgs.values() if m.role == "model"), key=lambda m: m.created_at)
    users = sorted((m for m in msgs.values() if m.role == "user"), key=lambda m: m.created_at)
    assert [m.text for m in users] == ["What is the work visa fee?", "Thanks, and how long does it take?"]
    assert [m.text for m in models] == ["The fee is 290 USD [1].", "About three weeks."]
    assert all(m.turn_id.startswith("voice_") and m.request_id.startswith("voice_") for m in msgs.values())
    first = models[0]
    assert first.charge is not None and first.charge.free and first.charge.round_index == 1
    assert [t.tool for t in first.tools] == ["search_knowledge", "google_search"]
    assert all(t.status == "completed" for t in first.tools)
    assert first.citations[0].title == "visa_guide.pdf" and first.citations[0].page_start == 2
    assert first.usage is not None and first.usage.model == "gemini-live-2.5-flash"
    assert first.usage.output_tokens == 50
    assert models[1].charge is not None and models[1].charge.charged_cents == 100

    conv = await conversation(cid)
    assert conv["rounds_ok"] == 2 and conv["message_count"] == 4
    assert conv["title"] == "What is the work visa fee?"
    assert await balance(visitor.uid) == 900
    assert any(row["category"] == "visitor_payment" for row in (await ledger(visitor.uid)).values())

    # The text chat history shows the voice transcript.
    resp = await client.get(
        f"/api/public/agents/{agent.slug}/conversations/{cid}", headers=dev_headers(visitor.uid)
    )
    assert resp.status_code == 200
    assert [m["text"] for m in resp.json()["messages"] if m["role"] == "model"] == [
        "The fee is 290 USD [1].",
        "About three weeks.",
    ]
    assert registry.get(visitor.uid) is None and not locks.is_busy(cid)
    assert voice_env.records.calls.extracted == []  # the agent has no record schema


async def test_free_rounds_are_shared_with_text_chat(app, client, voice_env) -> None:
    _, visitor, agent = await setup_agent(pricing={"free_rounds": 1, "price_cents": 100})
    await open_wallet(visitor.uid, 1_000)
    # Use the only free round in a voice call …
    ws = await start_call(app, agent, visitor)
    await ws.next_event("ready")
    live = await voice_env.connector.session()
    live.push(heard("Hi"), *answer("Hello!"))
    assert (await ws.next_event("round"))["billing"]["free"] is True
    await ws.send_json({"type": "stop"})
    await ws.drain()
    await ws.finish()
    # … then the billing status of the text chat says the next round costs money.
    resp = await client.get(f"/api/public/agents/{agent.slug}/billing", headers=dev_headers(visitor.uid))
    assert resp.json()["used_rounds"] == 1 and resp.json()["next_round_charge_cents"] == 100


# --------------------------------------------------------------------------- barge-in and endings


async def test_barge_in_mid_answer_is_aborted_and_not_charged(app, voice_env) -> None:
    _, visitor, agent = await setup_agent(pricing={"free_rounds": 0, "price_cents": 100})
    await open_wallet(visitor.uid, 1_000)
    ws = await start_call(app, agent, visitor)
    ready = await ws.next_event("ready")
    live = await voice_env.connector.session()
    live.push(heard("Tell me a long story"), audio(), said("Once upon a time"))
    speaking = await ws.next_event("speaking")
    frame = await ws.receive()
    while not isinstance(frame, bytes):
        frame = await ws.receive()
    assert len(frame) == 4800
    live.push(interrupted())
    event = await ws.next_event("interrupted")
    rnd = await ws.next_event("round")
    assert rnd["turn_id"] == event["turn_id"] == speaking["turn_id"]
    assert rnd["outcome"] == "aborted" and rnd["billing"]["charged_cents"] == 0
    # The next utterance gets its own (charged) round.
    live.push(heard("What is two plus two?"), *answer("Four."))
    rnd2 = await ws.next_event("round")
    assert rnd2["outcome"] == "ok" and rnd2["billing"]["charged_cents"] == 100
    await ws.disconnect()

    msgs = await messages_of(ready["conversation_id"])
    aborted = msgs[rnd["message_id"]]
    assert aborted.status == "aborted" and aborted.text == "Once upon a time" and aborted.charge is None
    assert await balance(visitor.uid) == 900
    assert (await conversation(ready["conversation_id"]))["rounds_ok"] == 1


async def test_interruption_after_the_answer_finished_keeps_the_round(app, voice_env) -> None:
    _, visitor, agent = await setup_agent(pricing={"free_rounds": 0, "price_cents": 100})
    await open_wallet(visitor.uid, 1_000)
    ws = await start_call(app, agent, visitor)
    await ws.next_event("ready")
    live = await voice_env.connector.session()
    # Generation completes (audio still playing in the browser), then the visitor speaks.
    live.push(heard("Tell me a story"), *answer("Once upon a time there was a dragon."), interrupted())
    await ws.next_event("interrupted")  # sent at once; the round settles in the background
    rnd = await ws.next_event("round")
    assert rnd["outcome"] == "ok" and rnd["billing"]["charged_cents"] == 100
    await ws.disconnect()


async def test_hang_up_mid_answer_saves_an_aborted_round(app, voice_env) -> None:
    _, visitor, agent = await setup_agent(pricing={"free_rounds": 0, "price_cents": 100})
    await open_wallet(visitor.uid, 1_000)
    ws = await start_call(app, agent, visitor)
    ready = await ws.next_event("ready")
    live = await voice_env.connector.session()
    live.push(heard("Explain everything"), audio(), said("Sure, first"))
    await ws.next_event("transcript")
    await ws.next_event("transcript")
    await asyncio.sleep(0.1)
    await ws.disconnect()
    assert live.closed
    msgs = await messages_of(ready["conversation_id"])
    model = next(m for m in msgs.values() if m.role == "model")
    assert model.status == "aborted" and model.charge is None
    assert await balance(visitor.uid) == 1_000


async def test_utterance_without_answer_is_not_saved(app, voice_env) -> None:
    _, visitor, agent = await setup_agent()
    ws = await start_call(app, agent, visitor)
    ready = await ws.next_event("ready")
    live = await voice_env.connector.session()
    live.push(heard("Hmm"))
    await ws.next_event("transcript")
    await ws.send_json({"type": "stop"})
    closed = await ws.next_event("closed")
    assert closed["conversation_id"] is None and closed["rounds"] == 0
    await ws.finish()
    assert await conversation(ready["conversation_id"]) is None


async def test_idle_timeout_ends_the_call(app, voice_env, monkeypatch) -> None:
    _, visitor, agent = await setup_agent()
    with settings_env(monkeypatch, VOICE_IDLE_TIMEOUT_S="1"):
        ws = await start_call(app, agent, visitor)
        await ws.next_event("ready")
        closed = await ws.next_event("closed", timeout=5)
    assert closed["reason"] == "idle"
    await ws.drain()
    assert ws.close_code == 1000
    await ws.finish()


async def test_max_duration_lets_the_current_answer_finish(app, voice_env, monkeypatch) -> None:
    _, visitor, agent = await setup_agent()
    with settings_env(monkeypatch, VOICE_MAX_SESSION_S="1"):
        ws = await start_call(app, agent, visitor)
        await ws.next_event("ready")
        live = await voice_env.connector.session()
        live.push(heard("One more question"))
        await asyncio.sleep(1.3)  # the limit passes while the visitor waits for the answer
        await ws.send_bytes(PCM_FRAME)  # no longer forwarded
        live.push(*answer("Here is the answer."))
        rnd = await ws.next_event("round")
        closed = await ws.next_event("closed", timeout=5)
    assert rnd["outcome"] == "ok"
    assert closed["reason"] == "max_duration" and closed["rounds"] == 1
    assert live.audio_frames == 0
    await ws.drain()
    await ws.finish()


# --------------------------------------------------------------------------- billing gates


async def test_refuses_to_start_when_the_visitor_cannot_pay(app, voice_env) -> None:
    _, visitor, agent = await setup_agent(pricing={"free_rounds": 0, "price_cents": 100})
    await open_wallet(visitor.uid, 40)
    ws = await start_call(app, agent, visitor)
    err = await ws.next_event("error")
    assert err["code"] == "visitor_insufficient_points" and err["trace_id"]
    assert err["detail"]["required_cents"] == 100 and err["detail"]["balance_cents"] == 40
    assert (await ws.next_event("closed"))["reason"] == "error"
    await ws.drain()
    assert ws.close_code == 4402
    await ws.finish()
    assert voice_env.connector.attempts == []  # never reached Gemini


async def test_refuses_when_the_creator_cannot_serve(app, voice_env) -> None:
    owner = await make_user("creator")
    visitor = await make_user()
    agent = await make_agent(owner, owner_balance_cents=0)
    ws = await start_call(app, agent, visitor)
    err = await ws.next_event("error")
    assert err["code"] == "insufficient_points"
    await ws.drain()
    assert ws.close_code == 4402
    await ws.finish()


async def test_ends_after_the_round_that_used_up_the_balance(app, voice_env) -> None:
    _, visitor, agent = await setup_agent(pricing={"free_rounds": 0, "price_cents": 100})
    await open_wallet(visitor.uid, 100)
    ws = await start_call(app, agent, visitor)
    await ws.next_event("ready")
    live = await voice_env.connector.session()
    live.push(heard("Question"), *answer("Answer."))
    rnd = await ws.next_event("round")
    assert rnd["billing"]["charged_cents"] == 100 and rnd["billing"]["balance_cents"] == 0
    err = await ws.next_event("error")
    assert err["code"] == "visitor_insufficient_points"
    closed = await ws.next_event("closed")
    assert closed["reason"] == "error" and closed["rounds"] == 1
    await ws.drain()
    assert ws.close_code == 4402
    await ws.finish()


async def test_conversation_limit(app, voice_env, monkeypatch) -> None:
    _, visitor, agent = await setup_agent()
    conv = await make_conversation(agent, visitor, turns=[("Hi", "Hello")])
    with settings_env(monkeypatch, MAX_ROUNDS_PER_CONVERSATION="2"):
        ws = await start_call(app, agent, visitor, conversation_id=conv.id)
        ready = await ws.next_event("ready")
        assert ready["conversation_id"] == conv.id
        live = await voice_env.connector.session()
        assert "Visitor: Hi\nYou: Hello" in live.config.system_instruction.parts[0].text
        live.push(heard("Again"), *answer("Sure."))
        await ws.next_event("round")
        err = await ws.next_event("error")
        assert err["code"] == "conversation_limit_reached"
        await ws.drain()
        await ws.finish()
        # Starting another call in the full conversation is refused up front.
        ws2 = await start_call(app, agent, visitor, conversation_id=conv.id)
        assert (await ws2.next_event("error"))["code"] == "conversation_limit_reached"
        await ws2.drain()
        assert ws2.close_code == 4400
        await ws2.finish()


async def test_owner_rounds_are_free(app, voice_env) -> None:
    owner, _, agent = await setup_agent(pricing={"free_rounds": 0, "price_cents": 100})
    ws = await start_call(app, agent, owner)
    await ws.next_event("ready")
    live = await voice_env.connector.session()
    live.push(heard("Test"), *answer("Works."))
    rnd = await ws.next_event("round")
    assert rnd["billing"]["charged_cents"] == 0 and rnd["billing"]["free"] is True
    await ws.disconnect()


# --------------------------------------------------------------------------- auth, scoping, locks


async def test_requires_credentials(app, voice_env) -> None:
    _, _, agent = await setup_agent()
    ws = await AsgiWebSocket(app, f"/api/public/agents/{agent.slug}/voice").connect()
    await ws.send_json({"type": "start"})
    err = await ws.next_event("error")
    assert err["code"] == "unauthenticated"
    await ws.drain()
    assert ws.close_code == 4401
    await ws.finish()


async def test_dev_identity_ignored_when_bypass_off(app, voice_env, monkeypatch) -> None:
    _, visitor, agent = await setup_agent()
    with settings_env(monkeypatch, AUTH_DEV_BYPASS="false"):
        ws = await start_call(app, agent, visitor)
        assert (await ws.next_event("error"))["code"] == "unauthenticated"
        await ws.drain()
        await ws.finish()


async def test_first_frame_must_be_start(app, voice_env) -> None:
    _, _, agent = await setup_agent()
    ws = await AsgiWebSocket(app, f"/api/public/agents/{agent.slug}/voice").connect()
    await ws.send_bytes(PCM_FRAME)
    assert (await ws.next_event("error"))["code"] == "validation_error"
    await ws.drain()
    assert ws.close_code == 4400
    await ws.finish()


async def test_start_timeout(app, voice_env, monkeypatch) -> None:
    _, _, agent = await setup_agent()
    with settings_env(monkeypatch, VOICE_AUTH_TIMEOUT_S="1"):
        ws = await AsgiWebSocket(app, f"/api/public/agents/{agent.slug}/voice").connect()
        assert (await ws.next_event("error", timeout=5))["code"] == "unauthenticated"
        await ws.drain()
        await ws.finish()


async def test_foreign_origin_is_rejected(app, voice_env) -> None:
    _, _, agent = await setup_agent()
    ws = await AsgiWebSocket(
        app, f"/api/public/agents/{agent.slug}/voice", {"origin": "https://evil.example"}
    ).connect()
    assert not ws.accepted and ws.close_code == 4403
    await ws.finish()
    ok = await AsgiWebSocket(
        app, f"/api/public/agents/{agent.slug}/voice", {"origin": "http://localhost:5173"}
    ).connect()
    assert ok.accepted
    await ok.disconnect()


async def test_other_visitors_conversation_is_not_found(app, voice_env) -> None:
    _, visitor, agent = await setup_agent()
    other = await make_user()
    conv = await make_conversation(agent, other)
    ws = await start_call(app, agent, visitor, conversation_id=conv.id)
    assert (await ws.next_event("error"))["code"] == "session_not_owned"
    await ws.drain()
    assert ws.close_code == 4404
    await ws.finish()


async def test_offline_agent(app, voice_env) -> None:
    _, visitor, agent = await setup_agent(status="paused")
    ws = await start_call(app, agent, visitor)
    assert (await ws.next_event("error"))["code"] == "agent_offline"
    await ws.drain()
    assert ws.close_code == 4410
    await ws.finish()


async def test_text_chat_is_locked_during_the_call(app, client, voice_env) -> None:
    _, visitor, agent = await setup_agent()
    conv = await make_conversation(agent, visitor)
    ws = await start_call(app, agent, visitor, conversation_id=conv.id)
    await ws.next_event("ready")
    resp = await client.post(
        f"/api/public/agents/{agent.slug}/chat",
        json={"conversation_id": conv.id, "turn_id": "t_12345678", "message": "typed"},
        headers=dev_headers(visitor.uid),
    )
    assert resp.status_code == 409 and resp.json()["code"] == "turn_in_progress"
    await ws.disconnect()
    assert not locks.is_busy(conv.id)


async def test_a_new_call_replaces_the_previous_one(app, voice_env) -> None:
    _, visitor, agent = await setup_agent()
    first = await start_call(app, agent, visitor)
    await first.next_event("ready")
    second = await start_call(app, agent, visitor)
    closed = await first.next_event("closed")
    assert closed["reason"] == "replaced"
    await first.drain()
    await first.finish()
    await second.next_event("ready")
    assert len(voice_env.connector.sessions) == 2
    await second.disconnect()
    assert registry.active_count() == 0


async def test_process_wide_session_cap(app, voice_env, monkeypatch) -> None:
    _, visitor, agent = await setup_agent()
    other = await make_user()
    with settings_env(monkeypatch, VOICE_MAX_SESSIONS="1"):
        first = await start_call(app, agent, visitor)
        await first.next_event("ready")
        second = await start_call(app, agent, other)
        err = await second.next_event("error")
        assert err["code"] == "rate_limited"
        await second.drain()
        assert second.close_code == 4429
        await second.finish()
        await first.disconnect()


# --------------------------------------------------------------------------- availability


async def test_falls_back_to_the_second_live_model(app, voice_env) -> None:
    voice_env.connector.fail_models = ("gemini-live-2.5-flash",)
    _, visitor, agent = await setup_agent()
    ws = await start_call(app, agent, visitor)
    ready = await ws.next_event("ready")
    assert ready["model"] == "gemini-live-2.5-flash-native-audio"
    assert voice_env.connector.attempts == [
        ("gemini-live-2.5-flash", "global"),
        ("gemini-live-2.5-flash-native-audio", "us-central1"),
    ]
    await ws.disconnect()


async def test_live_unavailable_is_a_clean_503(app, voice_env) -> None:
    voice_env.connector.fail_models = ("gemini-live-2.5-flash", "gemini-live-2.5-flash-native-audio")
    _, visitor, agent = await setup_agent()
    ws = await start_call(app, agent, visitor)
    err = await ws.next_event("error")
    assert err["code"] == "service_unavailable"
    await ws.drain()
    assert ws.close_code == 4503
    await ws.finish()


async def test_live_error_mid_answer_saves_a_failed_round(app, voice_env) -> None:
    _, visitor, agent = await setup_agent(pricing={"free_rounds": 0, "price_cents": 100})
    await open_wallet(visitor.uid, 1_000)
    ws = await start_call(app, agent, visitor)
    ready = await ws.next_event("ready")
    live = await voice_env.connector.session()
    live.push(heard("Question"), audio(), said("Partial"), RuntimeError("socket reset"))
    err = await ws.next_event("error")
    assert err["code"] == "service_unavailable"
    rnd = await ws.next_event("round")
    assert rnd["outcome"] == "failed" and rnd["billing"]["charged_cents"] == 0
    await ws.drain()
    await ws.finish()
    model = next(m for m in (await messages_of(ready["conversation_id"])).values() if m.role == "model")
    assert model.status == "failed" and model.error_code == "agent_run_failed"
    assert await balance(visitor.uid) == 1_000


async def test_voice_disabled(app, voice_env, monkeypatch) -> None:
    _, visitor, agent = await setup_agent()
    with settings_env(monkeypatch, VOICE_ENABLED="false"):
        ws = await start_call(app, agent, visitor)
        assert (await ws.next_event("error"))["code"] == "service_unavailable"
        await ws.drain()
        await ws.finish()


async def test_records_extraction_after_counted_rounds(app, voice_env) -> None:
    schema = {"fields": [{"key": "nationality", "label": "Nationality", "type": "text"}]}
    _, visitor, agent = await setup_agent(config={"record_schema": schema})
    ws = await start_call(app, agent, visitor)
    ready = await ws.next_event("ready")
    live = await voice_env.connector.session()
    assert "save_record" not in live.config.system_instruction.parts[0].text
    live.push(heard("I am from Brazil"), *answer("Thanks!"))
    await ws.next_event("round")
    await ws.disconnect()
    await asyncio.sleep(0.05)
    assert voice_env.records.calls.extracted == [ready["conversation_id"]]


async def test_ping_and_oversized_frames(app, voice_env) -> None:
    _, visitor, agent = await setup_agent()
    ws = await start_call(app, agent, visitor)
    await ws.next_event("ready")
    await ws.send_json({"type": "ping"})
    assert (await ws.next_event("pong"))["type"] == "pong"
    await ws.send_bytes(b"\x00" * (64 * 1024 + 2))
    assert (await ws.next_event("error"))["code"] == "validation_error"
    await ws.drain()
    assert ws.close_code == 4400
    await ws.finish()


async def test_config_endpoint(client) -> None:
    resp = await client.get("/api/public/voice/config")
    assert resp.status_code == 200
    assert resp.json() == {
        "enabled": True,
        "max_session_s": 540,
        "idle_timeout_s": 90,
        "input_sample_rate": 16000,
        "output_sample_rate": 24000,
    }


# --------------------------------------------------------------------------- review hardening


async def test_live_handshake_timeout_is_a_503_and_releases_everything(app, voice_env, monkeypatch) -> None:
    voice_env.connector.hang_models = ("gemini-live-2.5-flash", "gemini-live-2.5-flash-native-audio")
    _, visitor, agent = await setup_agent()
    conv = await make_conversation(agent, visitor)
    with settings_env(monkeypatch, VOICE_CONNECT_TIMEOUT_S="0.2"):
        ws = await start_call(app, agent, visitor, conversation_id=conv.id)
        err = await ws.next_event("error", timeout=5)
    assert err["code"] == "service_unavailable" and err["trace_id"]
    await ws.drain()
    assert ws.close_code == 4503
    await ws.finish()
    assert voice_env.connector.hung_cancelled == 2  # both plans tried, both half-open sockets closed
    assert registry.get(visitor.uid) is None and not locks.is_busy(conv.id)


async def test_voice_calls_take_no_text_turn_slot_but_count_per_account(app, voice_env, monkeypatch) -> None:
    _, visitor, agent = await setup_agent()
    with settings_env(monkeypatch, MAX_CONCURRENT_TURNS="1"):
        ws = await start_call(app, agent, visitor)
        await ws.next_event("ready")
        assert locks.active_turns() == 0
        locks.acquire_turn("cnv_text_elsewhere", "someone_else")  # the only text slot is still free
        locks.release_turn("cnv_text_elsewhere")
        await ws.disconnect()
    # The account's turn limit covers voice calls too.
    held = [f"cnv_busy_{i}" for i in range(locks.MAX_TURNS_PER_USER)]
    for cid in held:
        locks.acquire_turn(cid, visitor.uid)
    try:
        ws2 = await start_call(app, agent, visitor)
        assert (await ws2.next_event("error"))["code"] == "rate_limited"
        await ws2.drain()
        assert ws2.close_code == 4429
        await ws2.finish()
    finally:
        for cid in held:
            locks.release_turn(cid)
    assert registry.get(visitor.uid) is None


async def test_an_unanswered_utterance_does_not_hold_the_call_open(app, voice_env, monkeypatch) -> None:
    _, visitor, agent = await setup_agent()
    with settings_env(monkeypatch, VOICE_IDLE_TIMEOUT_S="1"):
        ws = await start_call(app, agent, visitor)
        await ws.next_event("ready")
        live = await voice_env.connector.session()
        live.push(heard("Hmm"))  # a backchannel the model does not answer
        closed = await ws.next_event("closed", timeout=5)
    assert closed["reason"] == "idle" and closed["conversation_id"] is None
    await ws.drain()
    await ws.finish()


async def test_a_stale_utterance_does_not_leak_into_the_next_round(app, voice_env, monkeypatch) -> None:
    monkeypatch.setattr(VoiceSession, "STALE_UTTERANCE_S", 0.2)
    _, visitor, agent = await setup_agent()
    ws = await start_call(app, agent, visitor)
    ready = await ws.next_event("ready")
    live = await voice_env.connector.session()
    live.push(heard("Hmm"))
    await ws.next_event("transcript")
    await asyncio.sleep(0.5)
    live.push(heard("What is the fee?"), *answer("290 USD."))
    assert (await ws.next_event("round"))["outcome"] == "ok"
    await ws.disconnect()
    users = [m for m in (await messages_of(ready["conversation_id"])).values() if m.role == "user"]
    assert [m.text for m in users] == ["What is the fee?"]


async def test_tool_calls_per_round_are_capped(app, voice_env, monkeypatch) -> None:
    _, visitor, agent = await setup_agent(pricing={"free_rounds": 0, "price_cents": 100})
    await open_wallet(visitor.uid, 1_000)
    with settings_env(monkeypatch, VOICE_MAX_TOOL_CALLS_PER_ROUND="1"):
        ws = await start_call(app, agent, visitor)
        ready = await ws.next_event("ready")
        live = await voice_env.connector.session()
        live.push(
            heard("Search forever"),
            tool_call("search_knowledge", {"query": "a"}, "c1"),
            tool_call("search_knowledge", {"query": "b"}, "c2"),
            tool_call("search_knowledge", {"query": "c"}, "c3"),
        )
        rnd = await ws.next_event("round")
        assert rnd["outcome"] == "failed" and rnd["billing"]["charged_cents"] == 0
        assert [[r.id for r in batch] for batch in live.tool_responses] == [["c1"], ["c2"], ["c3"]]
        assert "results" in live.tool_responses[0][0].response
        assert live.tool_responses[1][0].response == {
            "error": "Tool limit reached for this answer. Answer now without tools."
        }
        # The call goes on: the next utterance is a fresh round with its own tool budget.
        live.push(heard("Just answer"), tool_call("search_knowledge", {"query": "d"}, "c4"))
        await ws.next_event("tool")
        live.push(*answer("Here you go."))
        assert (await ws.next_event("round"))["outcome"] == "ok"
        await ws.disconnect()
    model = next(
        m
        for m in (await messages_of(ready["conversation_id"])).values()
        if m.role == "model" and m.status == "failed"
    )
    assert model.error_code == "agent_run_failed" and [t.tool for t in model.tools] == ["search_knowledge"]
    assert await balance(visitor.uid) == 900


async def test_close_after_go_away_is_a_normal_end(app, voice_env) -> None:
    _, visitor, agent = await setup_agent(pricing={"free_rounds": 0, "price_cents": 100})
    await open_wallet(visitor.uid, 1_000)
    ws = await start_call(app, agent, visitor)
    await ws.next_event("ready")
    live = await voice_env.connector.session()
    live.push(heard("Tell me more"), audio(), said("Well"), go_away(), RuntimeError("1011 session over"))
    items = await ws.drain()
    events = [i for i in items if isinstance(i, dict)]
    assert not any(e["type"] == "error" for e in events)
    rnd = next(e for e in events if e["type"] == "round")
    assert rnd["outcome"] == "aborted" and rnd["billing"]["charged_cents"] == 0
    assert events[-1]["type"] == "closed" and events[-1]["reason"] == "server"
    assert ws.close_code == 1000
    await ws.finish()
    assert await balance(visitor.uid) == 1_000
