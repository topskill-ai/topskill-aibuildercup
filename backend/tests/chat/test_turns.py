"""End-to-end chat turns with FakeLlm on the Firestore emulator (SPEC §7.4, §10.4, §12.1 chat)."""

from __future__ import annotations

import asyncio
import secrets

import pytest

from tests.chat.conftest import assert_protocol, parse_sse
from tests.conftest import dev_headers
from tests.core.helpers import settings_env
from topskill.chat import locks
from topskill.chat.runtime import TurnContext, start_turn
from topskill.db import Col, get_db, new_id
from topskill.domain import MessageDoc, Passage
from topskill.repo import get_active_version
from topskill.testing import fake_llm
from topskill.testing.factories import make_agent, make_conversation, make_user
from topskill.testing.fake_llm import call, empty, end, error, error_event, grounding, pause, text, thought

pytestmark = pytest.mark.emulator

SCHEMA = {
    "fields": [
        {"key": "nationality", "label": "Nationality", "type": "text"},
        {"key": "budget", "label": "Budget", "type": "number"},
    ]
}


def turn_id() -> str:
    return "t" + secrets.token_hex(6)


async def setup_agent(**overrides):
    owner = await make_user("creator")
    visitor = await make_user()
    agent = await make_agent(owner, **overrides)
    return owner, visitor, agent


async def post_chat(client, agent, user, message="Hello?", conversation_id=None, tid=None):
    resp = await client.post(
        f"/api/public/agents/{agent.slug}/chat",
        json={"conversation_id": conversation_id, "turn_id": tid or turn_id(), "message": message},
        headers=dev_headers(user.uid),
    )
    return resp


async def chat_frames(client, agent, user, **kw) -> tuple[list[tuple[str, dict]], dict]:
    resp = await post_chat(client, agent, user, **kw)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = parse_sse(resp.text)
    return frames, assert_protocol(frames)


async def messages_of(cid: str) -> dict[str, MessageDoc]:
    col = get_db().collection(Col.CONVERSATIONS).document(cid).collection(Col.MESSAGES)
    return {s.id: MessageDoc.model_validate(s.to_dict()) async for s in col.stream()}


async def conversation_doc(cid: str) -> dict | None:
    snap = await get_db().collection(Col.CONVERSATIONS).document(cid).get()
    return snap.to_dict() if snap.exists else None


# --------------------------------------------------------------------------- happy path


async def test_full_turn_with_knowledge_web_and_charge(client, doubles) -> None:
    billing, knowledge, _ = doubles
    knowledge.passages = [
        Passage(file_id="kf_1", filename="visa_guide.pdf", text="The work visa fee is 290 USD.", page_start=2,
                page_end=2, score=0.1)
    ]  # fmt: skip
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script(
        [
            call("search_knowledge", {"query": "work visa fee"}),
            thought("thinking out loud"),
            text(["The fee is ", "290 USD [1]."]),
            grounding(
                [("gov.br", "https://vertexaisearch.example/r1")],
                ["brazil work visa fee"],
                entry_point_html="<div>chips</div>",
            ),
        ]
    )
    frames, done = await chat_frames(client, agent, visitor, message="  How much is the work visa?  ")
    names = [n for n, _ in frames]
    assert names == [
        "status", "tool_start", "tool_end", "text_delta", "text_delta", "tool_start", "tool_end", "citations",
        "done",
    ]  # fmt: skip
    status = frames[0][1]
    cid = status["conversation_id"]
    assert cid.startswith("cnv_") and status["model"] == "gemini-3.8-flash"
    assert status["request_id"].startswith("turn_") and len(status["request_id"]) == 37
    assert frames[1][1] == {
        "id": frames[1][1]["id"], "tool": "search_knowledge", "kind": "search",
        "title": "Searched the knowledge base", "target": "work visa fee",
    }  # fmt: skip
    assert "".join(d["delta"] for n, d in frames if n == "text_delta") == "The fee is 290 USD [1]."
    assert frames[5][1]["id"] == "web-0" and frames[5][1]["kind"] == "web"
    cit = frames[7][1]
    assert [(c["n"], c["kind"]) for c in cit["sources"]] == [(1, "knowledge"), (2, "web")]
    assert cit["sources"][0]["page_start"] == 2 and cit["search_entry_point_html"] == "<div>chips</div>"
    assert cit["web_search_queries"] == ["brazil work visa fee"]
    assert done["outcome"] == "ok" and done["conversation_id"] == cid
    assert done["message_id"] == f"m_{status['request_id']}"
    assert done["billing"]["charged_cents"] == 100 and done["billing"]["free"] is False
    assert (
        done["usage"]["request_id"] == status["request_id"] and done["usage"]["model"] == "gemini-3.8-flash"
    )
    assert [s.succeeded for s in billing.settles] == [True]

    msgs = await messages_of(cid)
    rid = status["request_id"]
    user_msg, model_msg = msgs[f"u_{rid}"], msgs[f"m_{rid}"]
    assert user_msg.text == "How much is the work visa?" and user_msg.status == "ok"
    assert model_msg.text == "The fee is 290 USD [1]." and "thinking" not in model_msg.text
    assert model_msg.charge.charged_cents == 100 and model_msg.charge.round_index == 1
    assert [t.status for t in model_msg.tools] == ["completed", "completed"]
    assert model_msg.citations[0].file_id == "kf_1" and model_msg.search_entry_point_html
    assert model_msg.created_at >= user_msg.created_at
    conv = await conversation_doc(cid)
    assert conv["title"] == "How much is the work visa?" and conv["rounds_ok"] == 1
    assert (
        conv["message_count"] == 2
        and conv["visitor_uid"] == visitor.uid
        and conv["owner_uid"] == agent.owner_uid
    )
    # the knowledge tool got the turn's collector, the prompt mentions the documents
    assert knowledge.collectors[0] is not None
    req = fake_llm.created[0].requests[0]
    assert "Reference documents" in req.config.system_instruction
    assert req.config.thinking_config.thinking_level is not None
    assert any(t.google_search is not None for t in req.config.tools)  # native search next to a function tool


async def test_second_turn_uses_history_and_increments(client, doubles) -> None:
    _, visitor, agent = await setup_agent()
    conv = await make_conversation(agent, visitor, turns=[("My name is Lin", "Hi Lin!")])
    fake_llm.queue_script([text("Your name is Lin.")])
    _frames, done = await chat_frames(
        client, agent, visitor, message="What is my name?", conversation_id=conv.id
    )
    assert done["outcome"] == "ok" and done["conversation_id"] == conv.id
    contents = fake_llm.created[0].requests[0].contents
    texts = [(c.role, c.parts[0].text) for c in contents]
    assert texts == [("user", "My name is Lin"), ("model", "Hi Lin!"), ("user", "What is my name?")]
    doc = await conversation_doc(conv.id)
    assert doc["rounds_ok"] == 2 and doc["message_count"] == 4 and doc["title"] == "My name is Lin"


async def test_owner_round_is_free(client, doubles) -> None:
    owner, _, agent = await setup_agent()
    _frames, done = await chat_frames(client, agent, owner)
    assert (
        done["outcome"] == "ok" and done["billing"]["free"] is True and done["billing"]["charged_cents"] == 0
    )


# --------------------------------------------------------------------------- outcome rules (§7.4, §10.4)


async def test_empty_response_is_failed_and_not_charged(client, doubles) -> None:
    billing, _, _ = doubles
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([empty()])
    frames, done = await chat_frames(client, agent, visitor)
    # ADK turns an empty reply into an error event (MODEL_RETURNED_NO_CONTENT) → agent_run_failed
    assert [n for n, _ in frames] == ["status", "error", "done"]
    assert frames[1][1]["code"] == "agent_run_failed"
    assert done["outcome"] == "failed" and done["billing"]["charged_cents"] == 0
    assert [s.succeeded for s in billing.settles] == [False]
    msgs = await messages_of(done["conversation_id"])
    rid = frames[0][1]["request_id"]
    assert msgs[f"m_{rid}"].status == "failed" and msgs[f"m_{rid}"].charge is None
    assert msgs[f"u_{rid}"].status == "failed"
    assert (await conversation_doc(done["conversation_id"]))["rounds_ok"] == 0


async def test_tool_only_save_record_is_failed(client, doubles) -> None:
    billing, _, records = doubles
    _, visitor, agent = await setup_agent(config={"record_schema": SCHEMA})
    fake_llm.queue_script([call("save_record", {"fields": {"nationality": {"value": "br", "evidence": "x"},
                                                            "budget": {"value": 5, "evidence": "y"}}})])  # fmt: skip
    frames, done = await chat_frames(client, agent, visitor, message="I'm from Brazil, budget 5")
    starts = [d for n, d in frames if n == "tool_start"]
    assert [(s["tool"], s["kind"], s["title"], s["target"]) for s in starts] == [
        ("save_record", "record", "Saved notes", "2 fields")
    ]
    assert done["outcome"] == "failed" and [s.succeeded for s in billing.settles] == [False]
    assert records.calls.saved and records.calls.extracted == []  # no extraction for a failed round
    req = fake_llm.created[0].requests[0]
    assert "Notes to collect" in req.config.system_instruction


async def test_safety_event_is_agent_run_failed(client, doubles) -> None:
    billing, _, _ = doubles
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([error_event("SAFETY", "blocked by safety")])
    frames, done = await chat_frames(client, agent, visitor)
    assert [n for n, _ in frames] == ["status", "error", "done"]
    assert frames[1][1]["code"] == "agent_run_failed" and "blocked" not in frames[1][1]["message"]
    assert done["outcome"] == "failed" and [s.succeeded for s in billing.settles] == [False]
    msgs = await messages_of(done["conversation_id"])
    assert msgs[f"m_{frames[0][1]['request_id']}"].error_code == "agent_run_failed"


async def test_error_after_visible_text_is_failed_and_not_charged(client, doubles) -> None:
    billing, _, _ = doubles
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([text("Partial answer"), error(503)])
    fake_llm.queue_script([text("never used")])
    frames, done = await chat_frames(client, agent, visitor)
    assert [n for n, _ in frames] == ["status", "text_delta", "error", "done"]
    assert frames[2][1]["code"] == "agent_run_failed"
    assert done["outcome"] == "failed" and [s.succeeded for s in billing.settles] == [False]
    assert fake_llm.pending_scripts() == 1  # no fallback once output was produced


async def test_fallback_only_before_first_output(client, doubles) -> None:
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([error(429)])
    fake_llm.queue_script([text("From the fallback model.")])
    frames, done = await chat_frames(client, agent, visitor)
    assert [n for n, _ in frames] == ["status", "text_delta", "done"]
    assert done["outcome"] == "ok" and done["usage"]["model"] == "gemini-3-flash-preview"
    assert [m.model for m in fake_llm.created] == ["gemini-3.8-flash", "gemini-3-flash-preview"]
    fallback_req = fake_llm.created[1].requests[0]
    assert fallback_req.config.thinking_config.thinking_level is not None


async def test_rate_limited_after_whole_chain(client, doubles) -> None:
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([error(429)])
    fake_llm.queue_script([error(429)])
    frames, done = await chat_frames(client, agent, visitor)
    assert [n for n, _ in frames] == ["status", "error", "done"]
    assert frames[1][1]["code"] == "rate_limited" and done["outcome"] == "failed"


async def test_non_retriable_error_does_not_fall_back(client, doubles) -> None:
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([error(400)])
    fake_llm.queue_script([text("never")])
    frames, done = await chat_frames(client, agent, visitor)
    assert frames[1][1]["code"] == "agent_run_failed" and done["outcome"] == "failed"
    assert fake_llm.pending_scripts() == 1


async def test_idle_timeout(client, doubles, monkeypatch) -> None:
    billing, _, _ = doubles
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([text("Hello"), pause(5), text(" never")])
    with settings_env(monkeypatch, turn_idle_timeout_s="1"):
        frames, done = await chat_frames(client, agent, visitor)
    assert [n for n, _ in frames] == ["status", "text_delta", "error", "done"]
    assert frames[2][1]["code"] == "turn_timeout"
    assert done["outcome"] == "failed"  # a partial answer cut short by a timeout is not charged
    assert billing.settles[0].succeeded is False


async def test_max_llm_calls(client, doubles, monkeypatch) -> None:
    _, visitor, agent = await setup_agent(config={"record_schema": SCHEMA})
    loop = [call("save_record", {"fields": {}}) for _ in range(5)]
    fake_llm.queue_script(loop)
    with settings_env(monkeypatch, max_llm_calls_per_turn="2"):
        frames, done = await chat_frames(client, agent, visitor)
    assert frames[-2] == ("error", frames[-2][1]) and frames[-2][1]["code"] == "agent_run_failed"
    assert done["outcome"] == "failed"


async def test_bypass_search_agent_is_one_web_line(client, doubles) -> None:
    _, visitor, agent = await setup_agent(config={"model": "gemini-2.5-pro", "record_schema": SCHEMA})
    fake_llm.queue_script(
        [
            call("google_search_agent", {"request": "brazil visa fee 2026"}),
            # the search sub-agent shares the fake model:
            grounding([("gov.br", "https://r/1")], ["brazil visa fee 2026", "visa fee"]),
            text("Sub-agent summary."),
            end(),
            text("The fee is 290 USD."),
        ]
    )
    frames, done = await chat_frames(client, agent, visitor)
    web = [d for n, d in frames if n == "tool_start" and d["kind"] == "web"]
    assert [(w["tool"], w["target"]) for w in web] == [("google_search_agent", "brazil visa fee 2026")]
    assert done["outcome"] == "ok"
    assert "Sub-agent summary." not in "".join(d["delta"] for n, d in frames if n == "text_delta")
    req = fake_llm.created[0].requests[0]
    names = [fd.name for t in req.config.tools or [] for fd in (t.function_declarations or [])]
    assert "google_search_agent" in names and req.config.thinking_config.thinking_budget == 1024


# --------------------------------------------------------------------------- abort / disconnect


async def test_abort_endpoint_mid_stream(client, doubles) -> None:
    billing, _, _ = doubles
    _, visitor, agent = await setup_agent(config={"record_schema": SCHEMA})
    fake_llm.queue_script([call("save_record", {"fields": {"budget": {"value": 1}}}), text("Hel"), pause(30),
                           text("lo")])  # fmt: skip
    tid = turn_id()
    task = asyncio.create_task(post_chat(client, agent, visitor, tid=tid))
    for _ in range(200):
        await asyncio.sleep(0.05)
        llm = fake_llm.created[0] if fake_llm.created else None
        if llm is not None and len(llm.requests) >= 2:
            break
    await asyncio.sleep(0.1)
    cid = next(k[0] for k in locks._aborts if k[1] == tid)
    other = await make_user()
    resp = await client.post(f"/api/public/agents/{agent.slug}/chat/abort",
                             json={"conversation_id": cid, "turn_id": tid}, headers=dev_headers(other.uid))  # fmt: skip
    assert resp.json() == {"aborted": False}
    resp = await client.post(f"/api/public/agents/{agent.slug}/chat/abort",
                             json={"conversation_id": cid, "turn_id": tid}, headers=dev_headers(visitor.uid))  # fmt: skip
    assert resp.status_code == 200 and resp.json() == {"aborted": True}
    chat_resp = await asyncio.wait_for(task, 10)
    frames = parse_sse(chat_resp.text)
    done = assert_protocol(frames)
    assert done["outcome"] == "aborted" and "error" not in [n for n, _ in frames]
    assert [s.succeeded for s in billing.settles] == [False] and done["billing"]["charged_cents"] == 0
    msgs = await messages_of(cid)
    rid = frames[0][1]["request_id"]
    assert msgs[f"m_{rid}"].status == "aborted" and msgs[f"m_{rid}"].text == "Hel"
    assert not locks.is_busy(cid)
    assert (await client.post(f"/api/public/agents/{agent.slug}/chat/abort",
                              json={"conversation_id": cid, "turn_id": tid},
                              headers=dev_headers(visitor.uid))).json() == {"aborted": False}  # fmt: skip


async def _ctx(agent, visitor) -> TurnContext:
    version = await get_active_version(agent)
    return TurnContext(agent=agent, version=version, user=visitor, conversation_id=new_id("cnv"),
                       conversation_is_new=True, turn_id=turn_id(), request_id="turn_" + secrets.token_hex(16),
                       message="Hello")  # fmt: skip


async def test_disconnect_mid_stream_aborts_and_still_settles(doubles) -> None:
    billing, _, _ = doubles
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([text("Hello"), pause(30), text(" world")])
    ctx = await _ctx(agent, visitor)
    finished = asyncio.Event()
    frames = start_turn(ctx, on_finish=finished.set)
    async for name, _data in frames:
        if name == "text_delta":
            break
    await frames.aclose()  # client went away
    await asyncio.wait_for(finished.wait(), 10)
    assert ctx.abort.is_set()
    assert [s.succeeded for s in billing.settles] == [False]
    msgs = await messages_of(ctx.conversation_id)
    assert msgs[f"m_{ctx.request_id}"].status == "aborted"


async def test_stop_after_runner_finished_is_ok_and_charged(doubles) -> None:
    billing, _, _ = doubles
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([text("A complete answer.")])
    ctx = await _ctx(agent, visitor)
    billing.before_settle = lambda _rid: ctx.abort.set()  # stop/disconnect lands after the model finished
    finished = asyncio.Event()
    frames = [f async for f in start_turn(ctx, on_finish=finished.set)]
    await finished.wait()
    done = frames[-1][1]
    assert done.outcome == "ok" and done.billing.charged_cents == 100
    assert [s.succeeded for s in billing.settles] == [True]


async def test_disconnect_after_last_text_still_charges(doubles) -> None:
    billing, _, _ = doubles
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([text("Full answer.")])
    ctx = await _ctx(agent, visitor)
    finished = asyncio.Event()
    settled = asyncio.Event()
    billing.before_settle = lambda _rid: settled.set()
    frames = start_turn(ctx, on_finish=finished.set)
    async for name, _ in frames:
        if name == "text_delta":
            break
    await settled.wait()  # runner already finished; the client disconnects only now
    await frames.aclose()
    await asyncio.wait_for(finished.wait(), 10)
    assert [s.succeeded for s in billing.settles] == [True]
    assert billing.settles[0].charge.charged_cents == 100


# --------------------------------------------------------------------------- settlement failure


async def test_settlement_failure_new_conversation(client, doubles) -> None:
    billing, _, _ = doubles
    billing.fail_settlement = True
    _, visitor, agent = await setup_agent()
    frames, done = await chat_frames(client, agent, visitor)
    assert frames[-2][0] == "error" and frames[-2][1]["code"] == "service_unavailable"
    assert done["conversation_id"] is None and done["billing"] is None and done["message_id"] is None
    assert done["outcome"] == "ok"
    assert await conversation_doc(frames[0][1]["conversation_id"]) is None


async def test_settlement_failure_existing_conversation_keeps_id(client, doubles) -> None:
    billing, _, _ = doubles
    billing.fail_settlement = True
    _, visitor, agent = await setup_agent()
    conv = await make_conversation(agent, visitor)
    _frames, done = await chat_frames(client, agent, visitor, conversation_id=conv.id)
    assert done["conversation_id"] == conv.id and done["billing"] is None


# --------------------------------------------------------------------------- scoping, locks, gates


async def test_session_not_owned(client, doubles) -> None:
    _, visitor, agent = await setup_agent()
    _, other_visitor, other_agent = await setup_agent()
    mine = await make_conversation(agent, visitor)
    theirs = await make_conversation(agent, other_visitor)
    other_agents = await make_conversation(other_agent, visitor)
    for cid in (theirs.id, other_agents.id, "cnv_" + "0" * 20, "bad/id"):
        resp = await post_chat(client, agent, visitor, conversation_id=cid)
        assert resp.status_code == 404 and resp.json()["code"] == "session_not_owned", cid
        if "/" in cid:
            continue
        got = await client.get(f"/api/public/agents/{agent.slug}/conversations/{cid}",
                               headers=dev_headers(visitor.uid))  # fmt: skip
        assert got.status_code == 404 and got.json()["code"] == "session_not_owned"
    got = await client.get(f"/api/public/agents/{agent.slug}/conversations/{mine.id}",
                           headers=dev_headers(visitor.uid))  # fmt: skip
    assert got.status_code == 200
    body = got.json()
    assert set(body) == {"id", "agent_slug", "title", "rounds_ok", "created_at", "updated_at", "messages"}
    assert [m["role"] for m in body["messages"]] == ["user", "model"]
    assert body["messages"][0]["created_at"].endswith("Z") and "request_id" not in body["messages"][0]
    assert fake_llm.created == []


async def test_turn_in_progress_and_lock_release(client, doubles) -> None:
    _, visitor, agent = await setup_agent()
    conv = await make_conversation(agent, visitor)
    locks.acquire_turn(conv.id)
    try:
        resp = await post_chat(client, agent, visitor, conversation_id=conv.id)
        assert resp.status_code == 409 and resp.json()["code"] == "turn_in_progress"
    finally:
        locks.release_turn(conv.id)
    _, done = await chat_frames(client, agent, visitor, conversation_id=conv.id)
    assert done["outcome"] == "ok" and not locks.is_busy(conv.id)


async def test_preflight_error_releases_lock(client, doubles) -> None:
    from topskill.errors import ApiError

    billing, _, _ = doubles
    billing.preflight_error = ApiError(402, "visitor_insufficient_points", "Top up.", {"required_cents": 100})
    _, visitor, agent = await setup_agent()
    conv = await make_conversation(agent, visitor)
    before = locks.active_turns()
    resp = await post_chat(client, agent, visitor, conversation_id=conv.id)
    assert resp.status_code == 402 and resp.json()["code"] == "visitor_insufficient_points"
    assert not locks.is_busy(conv.id) and locks.active_turns() == before


async def test_conversation_limit_and_validation(client, doubles, monkeypatch) -> None:
    _, visitor, agent = await setup_agent()
    conv = await make_conversation(agent, visitor, turns=[("a", "b"), ("c", "d")])
    with settings_env(monkeypatch, max_rounds_per_conversation="2"):
        resp = await post_chat(client, agent, visitor, conversation_id=conv.id)
    assert resp.status_code == 400 and resp.json()["code"] == "conversation_limit_reached"
    for body in (
        {"conversation_id": None, "turn_id": "short", "message": "hi"},
        {"conversation_id": None, "turn_id": turn_id(), "message": "   "},
        {"conversation_id": None, "turn_id": turn_id(), "message": "x" * 8001},
    ):
        resp = await client.post(f"/api/public/agents/{agent.slug}/chat", json=body,
                                 headers=dev_headers(visitor.uid))  # fmt: skip
        assert resp.status_code == 400 and resp.json()["code"] == "validation_error"
    resp = await client.post(f"/api/public/agents/{agent.slug}/chat",
                             json={"conversation_id": None, "turn_id": turn_id(), "message": "hi"})  # fmt: skip
    assert resp.status_code == 401


async def test_offline_and_unknown_agent(client, doubles) -> None:
    _, visitor, agent = await setup_agent(status="paused")
    resp = await post_chat(client, agent, visitor)
    assert resp.status_code == 410 and resp.json()["code"] == "agent_offline"
    resp = await client.post("/api/public/agents/no-such-agent-zz/chat",
                             json={"conversation_id": None, "turn_id": turn_id(), "message": "hi"},
                             headers=dev_headers(visitor.uid))  # fmt: skip
    assert resp.status_code == 404 and resp.json()["code"] == "agent_not_found"


async def test_extraction_scheduled_when_save_record_not_called(client, doubles) -> None:
    _, _, records = doubles
    _, visitor, agent = await setup_agent(config={"record_schema": SCHEMA})
    _, done = await chat_frames(client, agent, visitor)
    for _ in range(50):
        if records.calls.extracted:
            break
        await asyncio.sleep(0.02)
    assert records.calls.extracted == [done["conversation_id"]]


# --------------------------------------------------------------------------- owner routes [SHOULD]


async def test_owner_conversation_routes(client, doubles) -> None:
    owner, visitor, agent = await setup_agent()
    _, _, other_agent = await setup_agent()
    conv = await make_conversation(agent, visitor, turns=[("Hello there", "Hi")])
    foreign = await make_conversation(other_agent, visitor)
    h = dev_headers(owner.uid)
    page = (await client.get(f"/api/agents/{agent.id}/conversations", headers=h)).json()
    assert [i["id"] for i in page["items"]] == [conv.id] and page["next_cursor"] is None
    item = page["items"][0]
    assert item["visitor_name"] == visitor.display_name and item["message_count"] == 2
    detail = await client.get(f"/api/agents/{agent.id}/conversations/{conv.id}", headers=h)
    assert detail.status_code == 200 and len(detail.json()["messages"]) == 2
    resp = await client.get(f"/api/agents/{agent.id}/conversations/{foreign.id}", headers=h)
    assert resp.status_code == 404 and resp.json()["code"] == "not_found"
    stranger = await make_user("creator")
    resp = await client.get(f"/api/agents/{agent.id}/conversations", headers=dev_headers(stranger.uid))
    assert resp.status_code == 404 and resp.json()["code"] == "agent_not_found"
    resp = await client.get(f"/api/agents/{agent.id}/conversations", headers=dev_headers(visitor.uid))
    assert resp.status_code == 403 and resp.json()["code"] == "creator_required"
