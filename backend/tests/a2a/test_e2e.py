"""End-to-end A2A calls through the real a2a-sdk client, in process (ASGI transport), with scripted
runtime agents and the Firestore billing double (SPEC §9.1, §9.3, §12.1 a2a)."""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from a2a.client import A2AClientError, ClientCallContext, ClientConfig, create_client
from a2a.types import Message, Part, Role, SendMessageRequest
from google.cloud.firestore import FieldFilter
from google.protobuf.json_format import MessageToDict

from tests.a2a.conftest import A2AEnv, issue_mandate
from tests.a2a.fakes import text_response
from tests.core.helpers import settings_env
from topskill.a2a import call_id_for, executor
from topskill.a2a.assistant import a2a_error_code, extract_result
from topskill.db import Col, get_db
from topskill.testing.factories import make_agent, make_user, open_wallet, unique

pytestmark = pytest.mark.emulator

BASE = "http://127.0.0.1:8000"


@asynccontextmanager
async def a2a_client(app, slug: str, *, streaming: bool = False):
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, timeout=30)
    client = await create_client(
        f"{BASE}/a2a/{slug}", client_config=ClientConfig(streaming=streaming, httpx_client=http)
    )
    try:
        yield client
    finally:
        await client.close()
        await http.aclose()


async def send(
    app,
    slug: str,
    text: str,
    token: str | None,
    *,
    message_id: str | None = None,
    streaming=False,
    via="header",
):
    async with a2a_client(app, slug, streaming=streaming) as client:
        req = SendMessageRequest(
            message=Message(
                message_id=message_id or uuid.uuid4().hex, role=Role.ROLE_USER, parts=[Part(text=text)]
            )
        )
        ctx = None
        if token and via == "header":
            ctx = ClientCallContext(service_parameters={"X-AP2-Mandate": token})
        elif token:
            req.metadata.update({"ap2_mandate": token})
        out = []
        async for item in client.send_message(req, context=ctx):
            out.append(MessageToDict(item[0] if isinstance(item, tuple) else item))
        return out


async def _doc(col: str, doc_id: str) -> dict:
    return (await get_db().collection(col).document(doc_id).get()).to_dict() or {}


async def _setup(client, *, price: int = 200, script=None, env: A2AEnv | None = None, **mandate_kw):
    owner = await make_user("creator")
    agent = await make_agent(owner, pricing={"a2a_price_cents": price}, title="Visa Advisor")
    if script is not None and env is not None:
        env.runtime.scripts[agent.slug] = script
    payer = unique("u_")
    await make_user("visitor", uid=payer)
    await open_wallet(payer, 10_000)
    mandate, token = await issue_mandate(client, payer, **mandate_kw)
    return owner, agent, payer, mandate, token


async def _no_running(mandate_id: str) -> None:
    calls = [
        s.to_dict()
        async for s in get_db()
        .collection(Col.A2A_CALLS)
        .where(filter=FieldFilter("mandate_id", "==", mandate_id))
        .stream()
    ]
    assert calls and all(c["status"] != "running" for c in calls), calls
    assert (await _doc(Col.MANDATES, mandate_id))["reserved_cents"] == 0


async def test_paid_call_is_charged_once(app, client, a2a_env: A2AEnv) -> None:
    owner, agent, payer, mandate, token = await _setup(client)
    message_id = uuid.uuid4().hex
    out = await send(app, agent.slug, "Plan a Brazil work visa", token, message_id=message_id)
    state, answer = extract_result(out)
    assert state == "TASK_STATE_COMPLETED" and answer.startswith("Here is the answer [1]")

    call_id = call_id_for(mandate["id"], message_id)
    call = await _doc(Col.A2A_CALLS, call_id)
    assert (
        call["status"] == "completed" and call["charged_cents"] == 200 and call["creator_share_cents"] == 160
    )
    assert call["answer_preview"].startswith("Here is the answer") and call["model"] == "scripted-model"
    assert call["task_id"] and call["request_preview"] == "Plan a Brazil work visa"
    m = await _doc(Col.MANDATES, mandate["id"])
    assert (m["spent_cents"], m["reserved_cents"], m["calls"]) == (200, 0, 1)
    assert (await _doc(Col.WALLETS, payer))["balance_cents"] == 9_800
    assert (await _doc(Col.WALLETS, owner.uid))["balance_cents"] == 10_160
    succeeded = [c for c in a2a_env.billing.settle_calls if c["succeeded"]]
    assert len(succeeded) == 1 and succeeded[0]["call_id"] == call_id

    # the bearer token never reaches the runtime: not in session events, not in the model request
    stack = await executor.get_stack(agent.slug)
    sessions = stack.executor._runner.session_service.sessions  # type: ignore[union-attr]
    assert list(sessions["topskill-a2a"]) == [f"a2a:{payer}"]
    dumped = json.dumps(
        [s.model_dump(mode="json") for s in sessions["topskill-a2a"][f"a2a:{payer}"].values()], default=str
    )
    assert token not in dumped
    assert token not in str(a2a_env.runtime.models[agent.slug].requests[-1].model_dump(mode="json"))

    # a replayed message is rejected with 402 mandate_replay and not charged again
    with pytest.raises(A2AClientError) as exc:
        await send(app, agent.slug, "again", token, message_id=message_id)
    assert a2a_error_code(exc.value) == ("mandate_replay", 402)
    assert (await _doc(Col.WALLETS, payer))["balance_cents"] == 9_800


async def test_mandate_in_metadata_and_streaming(app, client, a2a_env: A2AEnv) -> None:
    _, agent, payer, mandate, token = await _setup(client)
    out = await send(app, agent.slug, "Hello", token, via="metadata")
    assert extract_result(out)[0] == "TASK_STATE_COMPLETED"
    out = await send(app, agent.slug, "Hello again", token, streaming=True)
    state, answer = extract_result(out)
    assert state == "TASK_STATE_COMPLETED" and "Here is the answer" in answer
    await asyncio.sleep(0.05)  # the stream-end settlement check runs as a background task
    m = await _doc(Col.MANDATES, mandate["id"])
    assert (m["spent_cents"], m["reserved_cents"], m["calls"]) == (400, 0, 2)
    assert (await _doc(Col.WALLETS, payer))["balance_cents"] == 9_600
    await _no_running(mandate["id"])


async def test_model_exception_fails_and_is_not_charged(app, client, a2a_env: A2AEnv) -> None:
    owner, agent, payer, mandate, token = await _setup(
        client, script=[RuntimeError("model exploded")], env=a2a_env
    )
    message_id = uuid.uuid4().hex
    out = await send(app, agent.slug, "Plan a visa", token, message_id=message_id)
    assert extract_result(out)[0] == "TASK_STATE_FAILED"
    call = await _doc(Col.A2A_CALLS, call_id_for(mandate["id"], message_id))
    assert (
        call["status"] == "failed" and call["charged_cents"] == 0 and call["error_code"] == "agent_run_failed"
    )
    m = await _doc(Col.MANDATES, mandate["id"])
    assert (m["spent_cents"], m["reserved_cents"], m["calls"]) == (0, 0, 0)
    assert (await _doc(Col.WALLETS, payer))["balance_cents"] == 10_000
    assert (await _doc(Col.WALLETS, owner.uid))["balance_cents"] == 10_000
    await _no_running(mandate["id"])
    assert not executor.is_call_active(call["id"])


async def test_empty_answer_is_not_charged(app, client, a2a_env: A2AEnv) -> None:
    _, agent, _payer, mandate, token = await _setup(client, script=[text_response("")], env=a2a_env)
    await send(app, agent.slug, "Plan a visa", token)
    m = await _doc(Col.MANDATES, mandate["id"])
    assert (m["spent_cents"], m["reserved_cents"]) == (0, 0)
    await _no_running(mandate["id"])


async def test_timeout_fails_and_is_not_charged(
    app, client, a2a_env: A2AEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def slow(_request):
        await asyncio.sleep(5)
        return text_response("too late")

    _, agent, payer, mandate, token = await _setup(client, script=[slow], env=a2a_env)
    with settings_env(monkeypatch, turn_total_timeout_s="1"):
        out = await send(app, agent.slug, "Plan a visa", token)
    assert extract_result(out)[0] == "TASK_STATE_FAILED"
    m = await _doc(Col.MANDATES, mandate["id"])
    assert (m["spent_cents"], m["reserved_cents"]) == (0, 0)
    assert (await _doc(Col.WALLETS, payer))["balance_cents"] == 10_000
    await _no_running(mandate["id"])


async def test_server_gives_up_before_the_callers_timeout(
    app, client, a2a_env: A2AEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run never outlives A2A_CALL_TIMEOUT_S: the server fails and settles it first, so a caller that
    reports a timed-out call as free is right (the run is not charged later)."""

    async def slow(_request):
        await asyncio.sleep(6)
        return text_response("too late")

    _, agent, payer, mandate, token = await _setup(client, script=[slow], env=a2a_env)
    loop = asyncio.get_running_loop()
    with settings_env(monkeypatch, a2a_call_timeout_s="2"):
        assert executor.run_budget_s() == 1.5
        started = loop.time()
        out = await send(app, agent.slug, "Plan a visa", token)
        elapsed = loop.time() - started
    assert extract_result(out)[0] == "TASK_STATE_FAILED"
    assert elapsed < 2, elapsed
    await asyncio.sleep(0.5)
    m = await _doc(Col.MANDATES, mandate["id"])
    assert (m["spent_cents"], m["reserved_cents"], m["calls"]) == (0, 0, 0)
    assert (await _doc(Col.WALLETS, payer))["balance_cents"] == 10_000
    await _no_running(mandate["id"])


def test_run_budget_stays_inside_the_client_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    with settings_env(monkeypatch, a2a_call_timeout_s="120", turn_total_timeout_s="300"):
        assert executor.run_budget_s() == 110
    with settings_env(monkeypatch, a2a_call_timeout_s="120", turn_total_timeout_s="60"):
        assert executor.run_budget_s() == 60
    with settings_env(monkeypatch, a2a_call_timeout_s="1"):
        assert executor.run_budget_s() == 0.75


async def test_v03_message_send_and_read_methods(app, client, a2a_env: A2AEnv) -> None:
    _, agent, _payer, mandate, token = await _setup(client)
    body = {
        "jsonrpc": "2.0",
        "id": "r1",
        "method": "message/send",
        "params": {
            "message": {
                "messageId": uuid.uuid4().hex,
                "role": "user",
                "kind": "message",
                "parts": [{"kind": "text", "text": "Hi"}],
            },
            "configuration": {"blocking": False},  # forced back to blocking for paid calls
            "metadata": {"ap2_mandate": token},
        },
    }
    resp = await client.post(f"/a2a/{agent.slug}", json=body)
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["status"]["state"] == "completed", result
    task_id = result["id"]
    m = await _doc(Col.MANDATES, mandate["id"])
    assert (m["spent_cents"], m["reserved_cents"], m["calls"]) == (200, 0, 1)

    # read methods: verified, never reserved or charged; tasks are scoped to the payer
    get = {"jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": task_id}}
    resp = await client.post(
        f"/a2a/{agent.slug}", json=get, headers={"X-AP2-Mandate": token, "A2A-Version": "1.0"}
    )
    assert resp.status_code == 200 and resp.json()["result"]["id"] == task_id, resp.text
    other = unique("u_")
    _, other_token = await issue_mandate(client, other)
    resp = await client.post(
        f"/a2a/{agent.slug}", json=get, headers={"X-AP2-Mandate": other_token, "A2A-Version": "1.0"}
    )
    assert "error" in resp.json() and "result" not in resp.json()
    resp = await client.post(f"/a2a/{agent.slug}", json=get)
    assert resp.status_code == 402 and resp.json()["error"]["data"]["code"] == "mandate_missing"
    m = await _doc(Col.MANDATES, mandate["id"])
    assert (m["spent_cents"], m["calls"]) == (200, 1)


async def test_budget_exhausted_over_http(app, client, a2a_env: A2AEnv) -> None:
    _, agent, _payer, mandate, token = await _setup(client, price=300, max_amount_cents=300, budget_cents=500)
    await send(app, agent.slug, "first", token)
    with pytest.raises(A2AClientError) as exc:
        await send(app, agent.slug, "second", token)
    assert a2a_error_code(exc.value) == ("mandate_budget_exhausted", 402)
    m = await _doc(Col.MANDATES, mandate["id"])
    assert (m["spent_cents"], m["reserved_cents"], m["calls"]) == (300, 0, 1)
