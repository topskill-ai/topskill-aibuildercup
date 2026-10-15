"""Chat turn and A2A call spans end to end (FakeLlm, Firestore emulator)."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

import httpx
import pytest

from tests.chat.conftest import assert_protocol, parse_sse
from tests.conftest import dev_headers
from topskill.domain import Passage
from topskill.observability.tracing import instrument_app
from topskill.observability.turns import CHAT_TURN_SPAN
from topskill.testing import fake_llm
from topskill.testing.factories import make_agent, make_user
from topskill.testing.fake_llm import call, grounding, text

pytestmark = pytest.mark.emulator


@pytest.fixture
async def traced_client(otel) -> AsyncIterator[httpx.AsyncClient]:
    from topskill.main import create_app

    app = create_app()
    instrument_app(app, otel.provider)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _hex(n: int, width: int = 32) -> str:
    return format(n, f"0{width}x")


async def test_chat_turn_span_attributes_and_adk_children(otel, traced_client, doubles) -> None:
    _billing, knowledge, _ = doubles
    knowledge.passages = [
        Passage(
            file_id="kf_1", filename="guide.pdf", text="Fee is 290 USD.", page_start=2, page_end=2, score=0.1
        )
    ]
    owner = await make_user("creator")
    visitor = await make_user()
    agent = await make_agent(owner)
    fake_llm.queue_script(
        [
            call("search_knowledge", {"query": "fee"}),
            text(["The fee is ", "290 USD [1]."]),
            grounding([("gov.br", "https://vertexaisearch.example/r1")], ["brazil visa fee"]),
        ]
    )
    resp = await traced_client.post(
        f"/api/public/agents/{agent.slug}/chat",
        json={"conversation_id": None, "turn_id": "t" + secrets.token_hex(6), "message": "How much?"},
        headers=dev_headers(visitor.uid),
    )
    assert resp.status_code == 200
    done = assert_protocol(parse_sse(resp.text))
    assert done["outcome"] == "ok"

    turn = otel.one(CHAT_TURN_SPAN)
    attrs = dict(turn.attributes or {})
    assert attrs["topskill.agent_id"] == agent.id and attrs["topskill.slug"] == agent.slug
    assert attrs["topskill.channel"] == "web" and attrs["topskill.outcome"] == "ok"
    assert attrs["topskill.charged_cents"] == 100
    assert attrs["topskill.knowledge_searches"] == 1 and attrs["topskill.web_searches"] == 1
    assert attrs["topskill.model"] == "gemini-3.8-flash"
    assert attrs["topskill.ttft_ms"] >= 0 and attrs["topskill.latency_ms"] >= attrs["topskill.ttft_ms"]
    assert attrs["topskill.conversation_id"] == done["conversation_id"]
    assert attrs["topskill.request_id"] == done["usage"]["request_id"]
    assert "gen_ai.usage.input_tokens" in attrs and "gen_ai.usage.output_tokens" in attrs
    assert [e.name for e in turn.events] == ["first_token"]

    # The trace id the client sees is the OTel trace id of the request, and every span is in that trace.
    trace_id = resp.headers["x-trace-id"]
    assert trace_id == _hex(turn.context.trace_id)
    spans = otel.spans()
    server = [s for s in spans if s.kind.name == "SERVER"]
    assert len(server) == 1 and server[0].name == "POST /api/public/agents/{slug}/chat"
    assert turn.parent is not None and turn.parent.span_id == server[0].context.span_id
    names = {s.name for s in spans}
    adk = [s for s in spans if s.instrumentation_scope and s.instrumentation_scope.name == "gcp.vertex.agent"]
    assert adk, names
    assert all(_hex(s.context.trace_id) == trace_id for s in spans)
    assert any(n.startswith("invoke_agent") for n in names), names
    assert any(n.startswith("execute_tool search_knowledge") for n in names), names
    # ADK spans nest under the turn span, never beside it.
    by_id = {s.context.span_id: s for s in spans}

    def ancestors(span):
        while span.parent is not None and span.parent.span_id in by_id:
            span = by_id[span.parent.span_id]
            yield span

    for s in adk:
        assert turn in list(ancestors(s)), s.name
    # No visitor text in any span attribute (ADK content capture is off).
    for s in spans:
        for value in (s.attributes or {}).values():
            assert "How much?" not in str(value), s.name


async def test_failed_turn_span_is_error_and_not_charged(otel, traced_client, doubles) -> None:
    owner = await make_user("creator")
    visitor = await make_user()
    agent = await make_agent(owner)
    fake_llm.queue_script([])  # empty reply → failed, not charged
    resp = await traced_client.post(
        f"/api/public/agents/{agent.slug}/chat",
        json={"conversation_id": None, "turn_id": "t" + secrets.token_hex(6), "message": "Hi"},
        headers=dev_headers(visitor.uid),
    )
    done = assert_protocol(parse_sse(resp.text))
    assert done["outcome"] == "failed"
    turn = otel.one(CHAT_TURN_SPAN)
    attrs = dict(turn.attributes or {})
    assert attrs["topskill.outcome"] == "failed" and attrs["topskill.charged_cents"] == 0
    assert attrs["topskill.error_code"] == "agent_run_failed"
    assert turn.status.status_code.name == "ERROR"
    assert "topskill.ttft_ms" not in attrs


async def test_error_envelope_trace_id_is_the_otel_trace_id(otel, traced_client) -> None:
    incoming = secrets.token_hex(16)
    resp = await traced_client.get(
        "/api/public/agents/does-not-exist", headers={"X-Cloud-Trace-Context": f"{incoming}/1;o=1"}
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["trace_id"] == incoming == resp.headers["x-trace-id"]
    server = [s for s in otel.spans() if s.kind.name == "SERVER"]
    assert server and _hex(server[0].context.trace_id) == incoming


async def test_w3c_traceparent_is_continued(otel, traced_client) -> None:
    trace_hex, parent_hex = secrets.token_hex(16), secrets.token_hex(8)
    resp = await traced_client.get(
        "/api/public/agents/does-not-exist", headers={"traceparent": f"00-{trace_hex}-{parent_hex}-01"}
    )
    assert resp.json()["trace_id"] == trace_hex
    server = next(s for s in otel.spans() if s.kind.name == "SERVER")
    assert server.parent is not None and _hex(server.parent.span_id, 16) == parent_hex


async def test_health_is_not_sampled_but_still_gets_the_otel_trace_id(otel, traced_client) -> None:
    resp = await traced_client.get("/health")
    assert resp.status_code == 200
    assert len(resp.headers["x-trace-id"]) == 32
    assert otel.spans() == []  # health ratio 0 in the fixture sampler
