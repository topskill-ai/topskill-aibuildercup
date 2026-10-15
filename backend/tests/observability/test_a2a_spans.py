"""A2A call spans: route → executor span → ADK spans, one trace (scripted runtime, Firestore emulator)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI

from tests.a2a.conftest import A2AEnv
from tests.a2a.fakes import call_response, text_response
from tests.a2a.test_e2e import _setup, send
from topskill.a2a import call_id_for
from topskill.a2a.assistant import extract_result
from topskill.observability.tracing import instrument_app
from topskill.observability.turns import A2A_CALL_SPAN

pytestmark = pytest.mark.emulator


@pytest.fixture
async def traced_app(otel) -> AsyncIterator[FastAPI]:
    from topskill.main import create_app

    app = create_app()
    instrument_app(app, otel.provider)
    yield app


async def test_paid_a2a_call_span(otel, traced_app, client, a2a_env: A2AEnv) -> None:
    _, agent, payer, mandate, token = await _setup(client)
    otel.exporter.clear()
    message_id = uuid.uuid4().hex
    out = await send(traced_app, agent.slug, "Plan a Brazil work visa", token, message_id=message_id)
    assert extract_result(out)[0] == "TASK_STATE_COMPLETED"

    span = otel.one(A2A_CALL_SPAN)
    attrs = dict(span.attributes or {})
    assert attrs["topskill.channel"] == "a2a" and attrs["topskill.slug"] == agent.slug
    assert attrs["topskill.agent_id"] == agent.id
    assert attrs["topskill.call_id"] == call_id_for(mandate["id"], message_id)
    assert attrs["topskill.price_cents"] == 200 and attrs["topskill.payer_uid"] == payer
    assert attrs["topskill.outcome"] == "ok" and attrs["topskill.charged_cents"] == 200
    assert attrs["topskill.model"] == "scripted-model"
    assert attrs["gen_ai.usage.input_tokens"] == 10 and attrs["gen_ai.usage.output_tokens"] == 5
    assert attrs["topskill.ttft_ms"] >= 0
    assert span.status.status_code.name == "OK"

    spans = otel.spans()
    server = [s for s in spans if s.kind.name == "SERVER"]
    assert [s.name for s in server] == ["GET /a2a/{slug}/.well-known/agent-card.json", "POST /a2a/{slug}"]
    assert span.parent is not None and span.parent.span_id == server[1].context.span_id
    assert not [s for s in spans if s.instrumentation_scope.name == "a2a-python-sdk"]  # SDK spans off
    rpc = [s for s in spans if s.context.trace_id == span.context.trace_id]
    assert len(rpc) == len(spans) - 1  # everything but the card fetch is one trace
    by_id = {s.context.span_id: s for s in spans}
    adk = [s for s in spans if s.instrumentation_scope and s.instrumentation_scope.name == "gcp.vertex.agent"]
    assert adk
    for s in adk:
        chain = []
        cur = s
        while cur.parent is not None and cur.parent.span_id in by_id:
            cur = by_id[cur.parent.span_id]
            chain.append(cur)
        assert span in chain, s.name


async def test_failed_a2a_call_span_is_not_charged(otel, traced_app, client, a2a_env: A2AEnv) -> None:
    _, agent, _payer, _mandate, token = await _setup(
        client, script=[RuntimeError("model exploded")], env=a2a_env
    )
    otel.exporter.clear()
    out = await send(traced_app, agent.slug, "Plan a visa", token)
    assert extract_result(out)[0] == "TASK_STATE_FAILED"
    span = otel.one(A2A_CALL_SPAN)
    attrs = dict(span.attributes or {})
    assert attrs["topskill.outcome"] == "failed" and attrs["topskill.charged_cents"] == 0
    assert span.status.status_code.name == "ERROR"


async def test_streaming_a2a_call_counts_tools(otel, traced_app, client, a2a_env: A2AEnv) -> None:
    _, agent, _payer, _mandate, token = await _setup(
        client,
        script=[call_response("search_knowledge", {"query": "visa"}), text_response("Done [1].")],
        env=a2a_env,
    )
    otel.exporter.clear()
    out = await send(traced_app, agent.slug, "Plan a visa", token, streaming=True)
    assert extract_result(out)[0] == "TASK_STATE_COMPLETED"
    await asyncio.sleep(0.05)
    attrs = dict(otel.one(A2A_CALL_SPAN).attributes or {})
    assert attrs["topskill.outcome"] == "ok" and attrs["topskill.knowledge_searches"] == 1
    assert attrs["topskill.tool_calls"] == 1 and attrs["topskill.a2a_method"] == "SendStreamingMessage"
