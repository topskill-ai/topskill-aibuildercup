"""Live: spans exported through the Telemetry API OTLP endpoint are readable from the Cloud Trace API.

Run once with ``uv run pytest -m live tests/observability`` (needs ADC with trace write/read access on
project top-skill; Cloud Trace can take a minute or two to index a new trace).
"""

from __future__ import annotations

import asyncio
import time

import google.auth
import pytest
from google.auth.transport.requests import AuthorizedSession

from topskill.config import get_settings
from topskill.observability.tracing import CLOUD_PLATFORM_SCOPE, build_otlp_exporter, make_provider
from topskill.observability.turns import CHAT_TURN_SPAN, TurnTrace

pytestmark = pytest.mark.live

PROJECT = "top-skill"


def _read_trace(session: AuthorizedSession, trace_id: str) -> dict | None:
    resp = session.get(
        f"https://cloudtrace.googleapis.com/v1/projects/{PROJECT}/traces/{trace_id}", timeout=20
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def test_turn_span_reaches_cloud_trace() -> None:
    settings = get_settings().model_copy(update={"app_version": "live-test", "env": "test"})
    exporter = build_otlp_exporter(settings.trace_otlp_endpoint)
    provider = make_provider(settings, exporter=exporter, environ={})
    tracer = provider.get_tracer("topskill")
    with tracer.start_as_current_span(
        "POST /api/public/agents/{slug}/chat", attributes={"http.target": "/api/public/agents/live/chat"}
    ):
        with tracer.start_as_current_span(CHAT_TURN_SPAN) as span:
            turn = TurnTrace(span=span, channel="web")
            turn.model, turn.input_tokens, turn.output_tokens = "gemini-3.8-flash", 11, 7
            turn.outcome, turn.charged_cents, turn.knowledge_searches = "ok", 100, 1
            with tracer.start_as_current_span("call_llm"):
                await asyncio.sleep(0.01)
            turn.finish()
            trace_id = format(span.get_span_context().trace_id, "032x")
    assert provider.force_flush(30_000)
    provider.shutdown()

    credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
    session = AuthorizedSession(credentials)
    deadline = time.monotonic() + 300
    found = None
    while time.monotonic() < deadline:
        found = await asyncio.to_thread(_read_trace, session, trace_id)
        if found and len(found.get("spans", [])) >= 3:
            break
        await asyncio.sleep(10)
    assert found is not None, f"trace {trace_id} not readable from Cloud Trace"
    spans = {s["name"]: s for s in found["spans"]}
    assert set(spans) >= {"POST /api/public/agents/{slug}/chat", CHAT_TURN_SPAN, "call_llm"}, spans.keys()
    labels = spans[CHAT_TURN_SPAN].get("labels", {})
    assert labels.get("topskill.outcome") == "ok" and labels.get("topskill.charged_cents") == "100"
    assert labels.get("gen_ai.usage.input_tokens") == "11"
    print(f"cloud trace ok: {trace_id} spans={sorted(spans)}")
