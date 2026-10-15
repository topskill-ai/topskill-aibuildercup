"""``POST /a2a/{slug}`` request validation and JSON-RPC error bodies (SPEC §9.3)."""

from __future__ import annotations

import pytest
from google.cloud.firestore import FieldFilter

from tests.a2a.conftest import A2AEnv, issue_mandate
from topskill.db import Col, get_db
from topskill.testing.factories import make_agent, make_user, unique

pytestmark = pytest.mark.emulator


def _send(message_id: str = "m1", **params) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "SendMessage",
        "params": {
            "message": {"messageId": message_id, "role": "ROLE_USER", "parts": [{"text": "hi"}]},
            **params,
        },
    }


async def test_protocol_errors(client, a2a_env: A2AEnv) -> None:
    owner = await make_user("creator")
    agent = await make_agent(owner)
    url = f"/a2a/{agent.slug}"

    resp = await client.post(url, content=b"{not json", headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == -32700 and resp.json()["id"] is None

    resp = await client.post(url, json=[_send()])
    assert resp.status_code == 400 and resp.json()["error"]["code"] == -32600
    assert (
        resp.json()["error"]["data"]["code"] == "validation_error"
        and resp.json()["error"]["data"]["trace_id"]
    )

    resp = await client.post(url, json={"jsonrpc": "2.0", "id": 3, "method": "SendMessage"})
    assert resp.status_code == 400 and resp.json()["error"]["code"] == -32602 and resp.json()["id"] == 3

    resp = await client.post(
        url, json={"jsonrpc": "2.0", "id": "x", "method": "GetExtendedAgentCard", "params": {}}
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == -32601 and resp.json()["error"]["data"]["code"] == "not_found"
    assert a2a_env.runtime.built == []  # never dispatched


async def test_agent_and_mandate_errors(client, a2a_env: A2AEnv) -> None:
    owner = await make_user("creator")
    agent = await make_agent(owner)
    resp = await client.post("/a2a/no-such-agent-abc", json=_send())
    assert resp.status_code == 404 and resp.json()["error"]["data"]["code"] == "agent_not_found"
    assert resp.json()["error"]["code"] == -32001

    paused = await make_agent(owner, status="paused")
    resp = await client.post(f"/a2a/{paused.slug}", json=_send())
    assert resp.status_code == 410 and resp.json()["error"]["data"]["code"] == "agent_offline"

    resp = await client.post(f"/a2a/{agent.slug}", json=_send())
    assert resp.status_code == 402
    body = resp.json()
    assert body["jsonrpc"] == "2.0" and body["id"] == 7
    assert body["error"]["code"] == -32001 and body["error"]["message"]
    assert body["error"]["data"]["code"] == "mandate_missing" and body["error"]["data"]["detail"] == {}

    payer = unique("u_")
    _, token = await issue_mandate(client, payer, audience="some-other-agent")
    resp = await client.post(f"/a2a/{agent.slug}", json=_send(), headers={"X-AP2-Mandate": token})
    assert resp.status_code == 402 and resp.json()["error"]["data"]["code"] == "mandate_audience"

    # mandate in params.metadata is accepted too; missing messageId → -32602 after verification
    _, token = await issue_mandate(client, payer)
    bad = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "SendMessage",
        "params": {"message": {}, "metadata": {"ap2_mandate": token}},
    }
    resp = await client.post(f"/a2a/{agent.slug}", json=bad)
    assert resp.status_code == 400 and resp.json()["error"]["code"] == -32602


async def test_price_over_max_is_rejected_before_running(client, a2a_env: A2AEnv) -> None:
    owner = await make_user("creator")
    agent = await make_agent(owner, pricing={"a2a_price_cents": 600})
    payer = unique("u_")
    mandate, token = await issue_mandate(client, payer, max_amount_cents=500)
    resp = await client.post(f"/a2a/{agent.slug}", json=_send(), headers={"X-AP2-Mandate": token})
    assert resp.status_code == 402 and resp.json()["error"]["data"]["code"] == "mandate_exceeds_max"
    calls = [
        s
        async for s in get_db()
        .collection(Col.A2A_CALLS)
        .where(filter=FieldFilter("mandate_id", "==", mandate["id"]))
        .stream()
    ]
    assert calls == []
    assert a2a_env.runtime.models.get(agent.slug) is None or a2a_env.runtime.models[agent.slug].requests == []
