"""Enterprise Assistant SSE (SPEC §9.4, §7.5): discover, 2 paid calls, 1 rejected call, call limit."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from google.cloud.firestore import FieldFilter

from tests.a2a.conftest import A2AEnv, issue_mandate, parse_sse
from tests.a2a.fakes import ScriptedLlm, call_response, function_responses, text_response
from tests.conftest import dev_headers
from tests.core.helpers import settings_env
from topskill.a2a import assistant, directory
from topskill.db import Col, get_db
from topskill.testing.factories import make_agent, make_user, open_wallet, unique

pytestmark = pytest.mark.emulator

TASK = (
    "Our new hire from Brazil needs a work visa plan, a resume review and a quick check of the offer letter"
)


@pytest.fixture
def assistant_llm(app, monkeypatch: pytest.MonkeyPatch):
    holder: dict[str, ScriptedLlm] = {}

    def install(steps: list) -> ScriptedLlm:
        holder["llm"] = ScriptedLlm(steps=steps)
        return holder["llm"]

    monkeypatch.setattr(assistant, "build_model", lambda: holder["llm"])
    monkeypatch.setattr(
        assistant,
        "http_client",
        lambda timeout: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000", timeout=timeout
        ),
    )
    return install


async def _agents(tag: str):
    owner = await make_user("creator")
    visa = await make_agent(
        owner, title=f"Visa Advisor {tag}", tagline="Work visa plans", pricing={"a2a_price_cents": 200}
    )
    resume = await make_agent(
        owner, title=f"Resume Reviewer {tag}", tagline="Resume feedback", pricing={"a2a_price_cents": 200}
    )
    contract = await make_agent(
        owner,
        title=f"Contract Reviewer {tag}",
        tagline="Offer letter checks",
        pricing={"a2a_price_cents": 600},
    )
    return owner, visa, resume, contract


async def _payer(client) -> tuple[str, dict, str]:
    payer = unique("u_")
    await make_user("visitor", uid=payer)
    await open_wallet(payer, 10_000)
    mandate, token = await issue_mandate(client, payer, max_amount_cents=500, budget_cents=2000)
    return payer, mandate, token


async def _chat(client, payer: str, token: str, text: str = TASK) -> list[tuple[str, dict]]:
    resp = await client.post(
        "/api/demo/assistant/chat",
        json={"messages": [{"role": "user", "text": text}], "mandate_token": token},
        headers=dev_headers(payer),
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    return parse_sse(resp.text)


async def test_assistant_two_paid_calls_and_one_rejected(client, a2a_env: A2AEnv, assistant_llm) -> None:
    tag = unique("q")
    _owner, visa, resume, contract = await _agents(tag)
    payer, mandate, token = await _payer(client)

    def final(request) -> object:
        results = [r.response for r in function_responses(request) if r.name == "call_agent"]
        assert [r.get("ok") for r in results] == [True, True, False]
        assert results[2]["error_code"] == "mandate_exceeds_max"
        return text_response(
            "Per Visa Advisor: apply for a work visa. The contract check was skipped. Total: 4.00 credits."
        )

    llm = assistant_llm(
        [
            call_response("find_agents", {"need": f"{tag} specialists"}),
            call_response(
                "call_agent", {"slug": visa.slug, "request": "Brazilian new hire needs a work visa plan."}
            ),
            call_response("call_agent", {"slug": resume.slug, "request": "Review this resume."}),
            call_response("call_agent", {"slug": contract.slug, "request": "Check this offer letter."}),
            final,
        ]
    )
    events = await _chat(client, payer, token)
    names = [n for n, _ in events]
    assert names[0] == "status" and names[-1] == "done" and names.count("done") == 1
    status = events[0][1]
    assert status["conversation_id"] is None and status["request_id"].startswith("asst_")

    [discover] = [d for n, d in events if n == "a2a_discover"]
    assert {a["slug"] for a in discover["agents"]} == {visa.slug, resume.slug, contract.slug}
    by_slug = {a["slug"]: a for a in discover["agents"]}
    assert by_slug[contract.slug]["price_cents"] == 600 and by_slug[visa.slug]["card_url"].endswith(
        "agent-card.json"
    )

    calls = [d for n, d in events if n == "a2a_call"]
    results = [d for n, d in events if n == "a2a_result"]
    assert [c["slug"] for c in calls] == [visa.slug, resume.slug, contract.slug]
    assert calls[0]["call_id"] and calls[1]["call_id"] and calls[2]["call_id"] is None
    assert calls[2]["price_cents"] == 600 and calls[0]["price_cents"] == 200
    assert [r["status"] for r in results] == ["completed", "completed", "rejected"]
    assert [r["charged_cents"] for r in results] == [200, 200, 0]
    assert [r["creator_share_cents"] for r in results] == [160, 160, 0]
    assert results[2]["error_code"] == "mandate_exceeds_max" and results[2]["call_id"] is None
    assert results[1]["mandate_spent_cents"] == 400 and results[2]["mandate_spent_cents"] == 400
    assert results[2]["mandate_budget_cents"] == 2000 and results[2]["payer_balance_cents"] == 9_600
    assert results[0]["answer_preview"].startswith("Here is the answer")

    tool_starts = [d for n, d in events if n == "tool_start"]
    assert [t["kind"] for t in tool_starts] == ["search", "a2a", "a2a", "a2a"]
    assert tool_starts[1]["title"] == f"Called Visa Advisor {tag}" and tool_starts[1]["target"] == visa.slug
    tool_ends = [d for n, d in events if n == "tool_end"]
    assert [t["status"] for t in tool_ends] == ["completed", "completed", "completed", "error"]

    text = "".join(d["delta"] for n, d in events if n == "text_delta")
    assert "contract check was skipped" in text
    done = events[-1][1]
    assert done["outcome"] == "ok" and done["billing"] is None and done["conversation_id"] is None
    assert done["mandate"] == {"id": mandate["id"], "spent_cents": 400, "budget_cents": 2000, "calls": 2}
    assert done["usage"]["request_id"] == status["request_id"]
    assert "error" not in names

    # no call ever ran for the contract reviewer, nothing is left running
    calls_docs = [
        s.to_dict()
        async for s in get_db()
        .collection(Col.A2A_CALLS)
        .where(filter=FieldFilter("mandate_id", "==", mandate["id"]))
        .stream()
    ]
    assert sorted(c["agent_slug"] for c in calls_docs) == sorted([visa.slug, resume.slug])
    assert all(c["status"] == "completed" for c in calls_docs)
    assert len(llm.requests) == 5


async def test_assistant_call_limit(client, a2a_env: A2AEnv, assistant_llm) -> None:
    tag = unique("q")
    _, visa, _, _ = await _agents(tag)
    payer, _mandate, token = await _payer(client)
    steps = [call_response("call_agent", {"slug": visa.slug, "request": f"Question {i}"}) for i in range(4)]
    assistant_llm([*steps, text_response("Done after four attempts.")])
    events = await _chat(client, payer, token)
    results = [d for n, d in events if n == "a2a_result"]
    assert [r["status"] for r in results] == ["completed", "completed", "completed", "rejected"]
    assert results[3]["error_code"] == "call_limit" and results[3]["call_id"] is None
    assert len([n for n, _ in events if n == "a2a_call"]) == 3  # the 4th call is never announced
    assert events[-1][1]["mandate"]["calls"] == 3 and events[-1][1]["mandate"]["spent_cents"] == 600


async def test_assistant_failed_call_and_unknown_agent(client, a2a_env: A2AEnv, assistant_llm) -> None:
    tag = unique("q")
    _, visa, _, _ = await _agents(tag)
    a2a_env.runtime.scripts[visa.slug] = [RuntimeError("model down")]
    payer, mandate, token = await _payer(client)
    assistant_llm(
        [
            call_response("call_agent", {"slug": visa.slug, "request": "Plan a visa."}),
            call_response("call_agent", {"slug": "no-such-agent-qq", "request": "Hello."}),
            text_response("Both specialists were unavailable; nothing was charged."),
        ]
    )
    events = await _chat(client, payer, token)
    results = [d for n, d in events if n == "a2a_result"]
    assert [(r["status"], r["error_code"], r["charged_cents"]) for r in results] == [
        ("failed", "agent_run_failed", 0),
        ("rejected", "agent_not_found", 0),
    ]
    assert results[0]["call_id"] is not None
    assert events[-1][1]["mandate"] == {
        "id": mandate["id"],
        "spent_cents": 0,
        "budget_cents": 2000,
        "calls": 0,
    }


async def test_assistant_model_failure_ends_with_error_and_done(
    client, a2a_env: A2AEnv, assistant_llm
) -> None:
    payer, _, token = await _payer(client)
    assistant_llm([RuntimeError("upstream exploded")])
    events = await _chat(client, payer, token)
    names = [n for n, _ in events]
    assert names[0] == "status" and names[-2:] == ["error", "done"]
    assert events[-2][1]["code"] == "agent_run_failed" and events[-2][1]["trace_id"]
    assert events[-1][1]["outcome"] == "failed"


async def test_assistant_rejects_before_streaming(client, a2a_env: A2AEnv) -> None:
    payer, _, token = await _payer(client)
    url = "/api/demo/assistant/chat"
    body = {"messages": [{"role": "user", "text": "hi"}], "mandate_token": token}
    resp = await client.post(url, json=body)
    assert resp.status_code == 401
    resp = await client.post(url, json=body, headers=dev_headers(unique("u_")))
    assert resp.status_code == 403 and resp.json()["code"] == "forbidden"
    resp = await client.post(url, json={**body, "mandate_token": "garbage"}, headers=dev_headers(payer))
    assert resp.status_code == 402 and resp.json()["code"] == "mandate_invalid"
    bad = {"messages": [{"role": "model", "text": "hello"}], "mandate_token": token}
    resp = await client.post(url, json=bad, headers=dev_headers(payer))
    assert resp.status_code == 400 and resp.json()["code"] == "validation_error"
    too_many = {"messages": [{"role": "user", "text": "x"}] * 13, "mandate_token": token}
    resp = await client.post(url, json=too_many, headers=dev_headers(payer))
    assert resp.status_code == 400


def _assert_frames_closed(events: list[tuple[str, dict]]) -> None:
    """SPEC §7.4 rule 2: every tool_start has a tool_end and every a2a_call an a2a_result, all before
    error/done; exactly one done, last."""
    names = [n for n, _ in events]
    assert names[-1] == "done" and names.count("done") == 1
    starts = [d["id"] for n, d in events if n == "tool_start"]
    ends = [d["id"] for n, d in events if n == "tool_end"]
    assert sorted(starts) == sorted(ends), (starts, ends)
    calls = [n for n in names if n == "a2a_call"]
    results = [n for n in names if n == "a2a_result"]
    assert len(calls) <= len(results)
    tail = names.index("error") if "error" in names else len(names) - 1
    assert all(n not in ("tool_end", "a2a_result") for n in names[tail:]), names


async def test_slow_agent_fails_free_and_is_never_charged_later(
    client, a2a_env: A2AEnv, assistant_llm, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scripted agent outlives A2A_CALL_TIMEOUT_S: the server gives up first and settles the call as
    failed, so the stream's "not charged" stays true after the slow run would have finished."""
    tag = unique("q")
    _, visa, _, _ = await _agents(tag)

    async def slow(_request):
        await asyncio.sleep(5)
        return text_response("too late")

    a2a_env.runtime.scripts[visa.slug] = [slow]
    payer, mandate, token = await _payer(client)
    assistant_llm(
        [
            call_response("call_agent", {"slug": visa.slug, "request": "Plan a visa."}),
            text_response("The visa advisor timed out; nothing was charged."),
        ]
    )
    with settings_env(monkeypatch, a2a_call_timeout_s="3"):
        events = await _chat(client, payer, token)
    _assert_frames_closed(events)
    [call] = [d for n, d in events if n == "a2a_call"]
    [result] = [d for n, d in events if n == "a2a_result"]
    assert call["call_id"] and result["call_id"] == call["call_id"]
    assert (result["status"], result["error_code"], result["charged_cents"]) == (
        "failed",
        "agent_run_failed",
        0,
    )
    assert events[-1][1]["mandate"]["spent_cents"] == 0

    await asyncio.sleep(3)  # past the moment the slow run would have answered
    doc = (await get_db().collection(Col.A2A_CALLS).document(call["call_id"]).get()).to_dict()
    assert doc["status"] == "failed" and doc["charged_cents"] == 0
    m = (await get_db().collection(Col.MANDATES).document(mandate["id"]).get()).to_dict()
    assert (m["spent_cents"], m["reserved_cents"], m["calls"]) == (0, 0, 0)


@pytest.mark.parametrize("slow", ["find", "send"])
async def test_turn_timeout_closes_open_frames(
    client, a2a_env: A2AEnv, assistant_llm, monkeypatch: pytest.MonkeyPatch, slow: str
) -> None:
    tag = unique("q")
    _, visa, _, _ = await _agents(tag)
    payer, _mandate, token = await _payer(client)

    async def hang(*_args, **_kwargs):
        await asyncio.sleep(30)
        raise AssertionError("not cancelled")

    if slow == "find":
        monkeypatch.setattr(directory, "search", hang)
        first = call_response("find_agents", {"need": "visa help"})
    else:
        monkeypatch.setattr(assistant.AssistantRun, "_send", hang)
        first = call_response("call_agent", {"slug": visa.slug, "request": "Plan a visa."})
    assistant_llm([first, text_response("unreachable")])
    with settings_env(monkeypatch, turn_total_timeout_s="2", a2a_call_timeout_s="1"):
        events = await _chat(client, payer, token)
    _assert_frames_closed(events)
    names = [n for n, _ in events]
    assert names[-2:] == ["error", "done"] and events[-2][1]["code"] == "turn_timeout"
    [end] = [d for n, d in events if n == "tool_end"]
    assert end["status"] == "error"
    if slow == "send":
        [call] = [d for n, d in events if n == "a2a_call"]
        [result] = [d for n, d in events if n == "a2a_result"]
        assert result["call_id"] == call["call_id"] and call["call_id"]
        assert (result["status"], result["error_code"], result["charged_cents"]) == (
            "failed",
            "turn_timeout",
            0,
        )


async def test_call_is_not_started_without_time_for_it(
    client, a2a_env: A2AEnv, assistant_llm, monkeypatch: pytest.MonkeyPatch
) -> None:
    tag = unique("q")
    _, visa, _, _ = await _agents(tag)
    payer, mandate, token = await _payer(client)
    assistant_llm(
        [
            call_response("call_agent", {"slug": visa.slug, "request": "Plan a visa."}),
            text_response("There was no time left to ask the visa advisor."),
        ]
    )
    with settings_env(monkeypatch, turn_total_timeout_s="5", a2a_call_timeout_s="10"):
        events = await _chat(client, payer, token)
    _assert_frames_closed(events)
    assert not [n for n, _ in events if n == "a2a_call"]
    [result] = [d for n, d in events if n == "a2a_result"]
    assert (result["status"], result["error_code"], result["call_id"]) == ("rejected", "turn_timeout", None)
    assert events[-1][1]["outcome"] == "ok" and events[-1][1]["mandate"]["calls"] == 0
    assert mandate["id"] == events[-1][1]["mandate"]["id"]


async def test_precheck_infrastructure_failure_is_rejected(
    client, a2a_env: A2AEnv, assistant_llm, monkeypatch: pytest.MonkeyPatch
) -> None:
    tag = unique("q")
    _, visa, _, _ = await _agents(tag)
    payer, _mandate, token = await _payer(client)

    async def down(*_args, **_kwargs):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(assistant, "load_a2a_agent", down)
    assistant_llm(
        [
            call_response("call_agent", {"slug": visa.slug, "request": "Plan a visa."}),
            text_response("The marketplace is unavailable right now."),
        ]
    )
    events = await _chat(client, payer, token)
    _assert_frames_closed(events)
    [result] = [d for n, d in events if n == "a2a_result"]
    assert (result["status"], result["error_code"]) == ("rejected", "service_unavailable")
    assert events[-1][1]["outcome"] == "ok"
