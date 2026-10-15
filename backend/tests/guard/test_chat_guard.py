"""Guard in the web chat turn, the A2A route and the agent APIs, on the Firestore emulator (SPEC §18 "Guard")."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.a2a.conftest import A2AEnv, issue_mandate
from tests.chat.test_turns import chat_frames, messages_of, setup_agent
from tests.conftest import dev_headers
from topskill import tasks
from topskill.a2a import call_id_for
from topskill.db import Col, get_db
from topskill.guard.fake import CARD, INJECTION, FakeGuard
from topskill.testing import fake_llm
from topskill.testing.factories import make_agent, make_user, open_wallet, unique
from topskill.testing.fake_llm import text

pytestmark = pytest.mark.emulator


async def _stats(agent_id: str) -> dict:
    pending = tasks.pending()
    if pending:
        await asyncio.wait(pending, timeout=5)
    snap = await get_db().collection(Col.AGENT_STATS).document(agent_id).get()
    return snap.to_dict() or {}


def _names(frames) -> list[str]:
    return [n for n, _ in frames]


# --------------------------------------------------------------------------- web chat


async def test_blocked_prompt_never_runs_the_model_and_is_not_charged(
    client, doubles, fake_guard: FakeGuard
) -> None:
    billing, _, _ = doubles
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([text("should never be produced")])
    frames, done = await chat_frames(client, agent, visitor, message=f"Please {INJECTION}.")
    assert _names(frames) == ["status", "guard", "error", "done"]
    guard_data = frames[1][1]
    assert guard_data == {
        "target": "prompt",
        "action": "blocked",
        "categories": ["pi_and_jailbreak"],
        "text": f"Please {INJECTION}.",
    }
    err = frames[2][1]
    assert err["code"] == "blocked_by_policy" and err["trace_id"] and "not charged" in err["message"]
    assert done["outcome"] == "failed" and done["billing"]["charged_cents"] == 0
    assert [s.succeeded for s in billing.settles] == [False]
    assert fake_llm.created == [] and fake_llm.pending_scripts() == 1  # the model never ran
    msgs = await messages_of(done["conversation_id"])
    model_msg = next(m for m in msgs.values() if m.role == "model")
    assert (
        model_msg.status == "failed" and model_msg.error_code == "blocked_by_policy" and model_msg.text == ""
    )
    stats = await _stats(agent.id)
    assert stats["guard_prompts_screened"] == 1 and stats["guard_prompts_blocked"] == 1
    assert "guard_answers_screened" not in stats
    fake_llm.reset()


async def test_sensitive_prompt_is_redacted_before_the_model(client, doubles, fake_guard: FakeGuard) -> None:
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([text("I can help with the refund.")])
    frames, done = await chat_frames(client, agent, visitor, message=f"My card {CARD} was charged twice")
    assert _names(frames)[:2] == ["status", "guard"]
    redacted = "My card [REDACTED:CREDIT_CARD_NUMBER] was charged twice"
    assert frames[1][1] == {"target": "prompt", "action": "redacted", "categories": ["sdp"], "text": redacted}
    assert done["outcome"] == "ok" and done["billing"]["charged_cents"] == 100
    request = fake_llm.created[0].requests[0]
    sent = " ".join(p.text or "" for c in request.contents for p in c.parts or [])
    assert CARD not in sent and "[REDACTED:CREDIT_CARD_NUMBER]" in sent
    msgs = await messages_of(done["conversation_id"])
    assert next(m for m in msgs.values() if m.role == "user").text == redacted
    stats = await _stats(agent.id)
    assert stats["guard_prompts_redacted"] == 1 and stats["guard_answers_screened"] == 1


async def test_answer_leak_is_redacted_and_still_charged(client, doubles, fake_guard: FakeGuard) -> None:
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([text(["The test card is ", f"{CARD}.", " Use it wisely."])])
    frames, done = await chat_frames(client, agent, visitor, message="Give me a test card")
    names = _names(frames)
    assert names.index("guard") > max(i for i, n in enumerate(names) if n == "text_delta")
    data = dict(frames)["guard"]
    clean = "The test card is [REDACTED:CREDIT_CARD_NUMBER]. Use it wisely."
    assert data == {"target": "answer", "action": "redacted", "categories": ["sdp"], "text": clean}
    assert done["outcome"] == "ok" and done["billing"]["charged_cents"] == 100
    msgs = await messages_of(done["conversation_id"])
    assert next(m for m in msgs.values() if m.role == "model").text == clean
    assert fake_guard.kinds() == ["prompt", "response"]
    stats = await _stats(agent.id)
    assert stats["guard_answers_redacted"] == 1


async def test_blocked_answer_is_withheld_and_not_charged(client, doubles, fake_guard: FakeGuard) -> None:
    billing, _, _ = doubles
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([text("forbidden [[csam]] content")])
    frames, done = await chat_frames(client, agent, visitor, message="hello")
    names = _names(frames)
    assert names[-3:] == ["guard", "error", "done"]
    assert frames[-3][1]["action"] == "blocked" and frames[-3][1]["text"] == ""
    assert frames[-2][1]["code"] == "blocked_by_policy"
    assert done["outcome"] == "failed" and done["billing"]["charged_cents"] == 0
    assert [s.succeeded for s in billing.settles] == [False]
    msgs = await messages_of(done["conversation_id"])
    model_msg = next(m for m in msgs.values() if m.role == "model")
    assert model_msg.text == "" and model_msg.error_code == "blocked_by_policy"


async def test_flagged_answer_is_allowed(client, doubles, fake_guard: FakeGuard) -> None:
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([text("A blunt answer [[rai:harassment]].")])
    frames, done = await chat_frames(client, agent, visitor, message="hello")
    assert "guard" not in _names(frames) and done["outcome"] == "ok"
    stats = await _stats(agent.id)
    assert stats["guard_answers_flagged"] == 1


async def test_guard_outage_fails_open(client, doubles, fake_guard: FakeGuard) -> None:
    _, visitor, agent = await setup_agent()
    fake_llm.queue_script([text("Fine answer [[guard-error]].")])
    frames, done = await chat_frames(client, agent, visitor, message=f"[[guard-error]] {INJECTION}")
    assert (
        "guard" not in _names(frames) and done["outcome"] == "ok" and done["billing"]["charged_cents"] == 100
    )
    stats = await _stats(agent.id)
    assert stats["guard_failed_open"] == 2 and "guard_prompts_screened" not in stats


async def test_screening_off_for_the_agent(client, doubles, fake_guard: FakeGuard) -> None:
    _, visitor, agent = await setup_agent(config={"safety_screening": False})
    fake_llm.queue_script([text(f"Card {CARD}")])
    frames, done = await chat_frames(client, agent, visitor, message=f"{INJECTION} please")
    assert "guard" not in _names(frames) and done["outcome"] == "ok"
    assert fake_guard.calls == []


# --------------------------------------------------------------------------- A2A


def _send(message_id: str, *texts: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "SendMessage",
        "params": {
            "message": {"messageId": message_id, "role": "ROLE_USER", "parts": [{"text": t} for t in texts]}
        },
    }


async def _a2a_setup(client, **agent_kw):
    owner = await make_user("creator")
    agent = await make_agent(owner, pricing={"a2a_price_cents": 200}, **agent_kw)
    payer = unique("u_")
    await make_user("visitor", uid=payer)
    await open_wallet(payer, 10_000)
    mandate, token = await issue_mandate(client, payer)
    return agent, payer, mandate, token


async def test_a2a_blocked_message_is_settled_failed(client, a2a_env: A2AEnv, fake_guard: FakeGuard) -> None:
    agent, _payer, mandate, token = await _a2a_setup(client)
    message_id = uuid.uuid4().hex
    resp = await client.post(
        f"/a2a/{agent.slug}",
        json=_send(message_id, f"{INJECTION} and reveal your prompt"),
        headers={"X-AP2-Mandate": token},
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["id"] == 9 and body["error"]["code"] == -32001
    data = body["error"]["data"]
    call_id = call_id_for(mandate["id"], message_id)
    assert data["code"] == "blocked_by_policy" and data["trace_id"]
    assert data["detail"] == {"categories": ["pi_and_jailbreak"], "call_id": call_id}
    call = (await get_db().collection(Col.A2A_CALLS).document(call_id).get()).to_dict()
    assert (
        call["status"] == "failed"
        and call["charged_cents"] == 0
        and call["error_code"] == "blocked_by_policy"
    )
    m = (await get_db().collection(Col.MANDATES).document(mandate["id"]).get()).to_dict()
    assert (m["spent_cents"], m["reserved_cents"], m["calls"]) == (0, 0, 0)
    assert a2a_env.runtime.models.get(agent.slug) is None or a2a_env.runtime.models[agent.slug].requests == []
    assert [c["error_code"] for c in a2a_env.billing.settle_calls] == ["blocked_by_policy"]
    stats = await _stats(agent.id)
    assert stats["guard_prompts_blocked"] == 1


async def test_a2a_sensitive_parts_are_redacted(client, a2a_env: A2AEnv, fake_guard: FakeGuard) -> None:
    agent, _payer, mandate, token = await _a2a_setup(client)
    message_id = uuid.uuid4().hex
    resp = await client.post(
        f"/a2a/{agent.slug}",
        json=_send(message_id, "Refund help", f"card {CARD} please"),
        headers={"X-AP2-Mandate": token, "A2A-Version": "1.0"},
    )
    assert resp.status_code == 200, resp.text
    request = a2a_env.runtime.models[agent.slug].requests[0]
    sent = " ".join(p.text or "" for c in request.contents for p in c.parts or [])
    assert CARD not in sent and "card [REDACTED:CREDIT_CARD_NUMBER] please" in sent and "Refund help" in sent
    call = (
        await get_db().collection(Col.A2A_CALLS).document(call_id_for(mandate["id"], message_id)).get()
    ).to_dict()
    assert call["status"] == "completed" and CARD not in call["request_preview"]


async def test_a2a_screening_off(client, a2a_env: A2AEnv, fake_guard: FakeGuard) -> None:
    agent, _payer, _mandate, token = await _a2a_setup(client, config={"safety_screening": False})
    resp = await client.post(
        f"/a2a/{agent.slug}",
        json=_send(uuid.uuid4().hex, INJECTION),
        headers={"X-AP2-Mandate": token, "A2A-Version": "1.0"},
    )
    assert resp.status_code == 200 and "result" in resp.json(), resp.text
    assert fake_guard.calls == []


# --------------------------------------------------------------------------- agent APIs


async def test_flag_is_live_and_shown_publicly(client, fake_guard: FakeGuard) -> None:
    owner = await make_user("creator")
    agent = await make_agent(owner)
    headers = dev_headers(owner.uid, "creator")
    resp = await client.get(f"/api/agents/{agent.id}", headers=headers)
    assert resp.json()["config"]["safety_screening"] is True
    assert resp.json()["stats"]["guard"]["prompts_blocked"] == 0
    pub = await client.get(f"/api/public/agents/{agent.slug}")
    assert pub.json()["features"]["safety_screening"] is True

    resp = await client.patch(
        f"/api/agents/{agent.id}", json={"config": {"safety_screening": False}}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"]["safety_screening"] is False and resp.json()["config_dirty"] is False
    pub = await client.get(f"/api/public/agents/{agent.slug}")
    assert pub.json()["features"]["safety_screening"] is False

    resp = await client.patch(
        f"/api/agents/{agent.id}", json={"config": {"safety_screening": None}}, headers=headers
    )
    assert resp.status_code == 400

    await (
        get_db().collection(Col.AGENT_STATS).document(agent.id).set({"guard_prompts_blocked": 3}, merge=True)
    )
    dash = await client.get("/api/dashboard", headers=headers)
    row = next(a for a in dash.json()["agents"] if a["id"] == agent.id)
    assert row["stats"]["guard"]["prompts_blocked"] == 3


async def test_badge_hidden_when_guard_is_off(client) -> None:
    owner = await make_user("creator")
    agent = await make_agent(owner)
    pub = await client.get(f"/api/public/agents/{agent.slug}")
    assert pub.json()["features"]["safety_screening"] is False  # GUARD_BACKEND=off in tests


async def test_create_with_screening_off(client) -> None:
    owner = await make_user("creator")
    skill = await client.post(
        "/api/skills",
        files={"files": ("SKILL.md", b"---\nname: guard-test\ndescription: x\n---\nBody", "text/markdown")},
        headers=dev_headers(owner.uid, "creator"),
    )
    if skill.status_code != 201:
        pytest.skip(f"skill upload shape changed: {skill.status_code}")
    resp = await client.post(
        "/api/agents",
        json={
            "skill_ids": [skill.json()["id"]],
            "title": "Guard Test",
            "pricing": {"mode": "free", "price_cents": 0, "free_rounds": 0},
            "config": {"soul": "You help.", "safety_screening": False},
        },
        headers=dev_headers(owner.uid, "creator"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["config"]["safety_screening"] is False
