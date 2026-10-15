"""Tracing setup, sampler, exporter wrapper, turn aggregation and log correlation (no emulator)."""

from __future__ import annotations

import contextvars
import json
import logging
from types import SimpleNamespace

import pytest
from google.adk.events import Event
from google.genai import types
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.sampling import Decision
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from topskill.config import Settings
from topskill.errors import _JsonFormatter, _TraceIdFilter, reset_trace_id, set_trace_id
from topskill.events import (
    CitationsData,
    DoneBilling,
    DoneData,
    DoneUsage,
    EventName,
    StatusData,
    StreamErrorData,
    TextDeltaData,
    ToolEndData,
    ToolStartData,
)
from topskill.observability import tracing
from topskill.observability.tracing import (
    FailOpenExporter,
    RouteSampler,
    build_resource,
    console_url,
    route_template,
    setup_tracing,
)
from topskill.observability.turns import TurnTrace, a2a_call_span, note_a2a_call, record_a2a_charge

pytestmark = pytest.mark.unit

TRACE = 0x0AF7651916CD43DD8448EB211C80319C


def _sampler(turn: float = 1.0, default: float = 1.0, health: float = 0.0) -> RouteSampler:
    return RouteSampler(turn_ratio=turn, default_ratio=default, health_ratio=health)


def _decide(sampler: RouteSampler, name: str, attrs: dict | None = None, parent=None) -> bool:
    ctx = trace.set_span_in_context(NonRecordingSpan(parent)) if parent is not None else None
    return sampler.should_sample(ctx, TRACE, name, attributes=attrs).decision.is_sampled()


# --------------------------------------------------------------------------- sampler


def test_sampler_routes() -> None:
    s = _sampler(turn=1.0, default=0.0, health=0.0)
    assert _decide(s, "POST /api/public/agents/x/chat", {"http.target": "/api/public/agents/x/chat"})
    assert _decide(s, "POST", {"url.path": "/a2a/visa-advisor", "http.request.method": "POST"})
    assert _decide(s, "POST", {"http.target": "/api/demo/assistant/chat", "http.method": "POST"})
    assert _decide(s, "GET", {"http.target": "/api/public/agents/x/voice"})
    assert not _decide(s, "GET", {"http.target": "/health"})
    assert not _decide(s, "GET", {"http.target": "/api/public/meta"})
    assert not _decide(s, "OPTIONS", {"http.target": "/api/public/agents/x/chat", "http.method": "OPTIONS"})
    assert not _decide(s, "GET", {"http.target": "/a2a/x/.well-known/agent-card.json"})  # default ratio
    assert not _decide(s, "GET", {"http.target": "/api/me"})
    assert not _decide(s, "background-work")  # no path → default ratio
    assert _decide(_sampler(default=1.0), "GET", {"http.target": "/api/me"})
    assert _decide(_sampler(health=1.0), "GET", {"http.target": "/healthz"})
    assert "turn=1.0" in s.get_description()


def test_sampler_parents() -> None:
    s = _sampler(turn=1.0, default=0.0, health=0.0)
    sampled_remote = SpanContext(TRACE, 0x1, is_remote=True, trace_flags=TraceFlags(TraceFlags.SAMPLED))
    unsampled_remote = SpanContext(TRACE, 0x1, is_remote=True, trace_flags=TraceFlags(TraceFlags.DEFAULT))
    unsampled_local = SpanContext(TRACE, 0x1, is_remote=False, trace_flags=TraceFlags(TraceFlags.DEFAULT))
    sampled_local = SpanContext(TRACE, 0x1, is_remote=False, trace_flags=TraceFlags(TraceFlags.SAMPLED))
    # A sampled caller (e.g. the assistant's traceparent) is always honoured, even on a health route.
    assert _decide(s, "GET", {"http.target": "/health"}, sampled_remote)
    # Cloud Run's front end sends o=0 for most requests: turns are still traced.
    assert _decide(s, "POST", {"http.target": "/a2a/x"}, unsampled_remote)
    assert not _decide(s, "GET", {"http.target": "/api/me"}, unsampled_remote)
    # Local children follow their parent whatever the route says.
    assert not _decide(s, "POST", {"http.target": "/a2a/x"}, unsampled_local)
    assert _decide(s, "GET", {"http.target": "/health"}, sampled_local)


# --------------------------------------------------------------------------- setup


def _settings(**kw) -> Settings:
    base = {"env": "test", "tracing_enabled": True, "app_version": "v1"}
    return Settings(**{**base, **kw})


def test_setup_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_provider", None)
    assert setup_tracing(None, _settings(tracing_enabled=False)) is None
    assert not tracing.tracing_active() and tracing.force_flush() is True


def test_setup_fails_open_without_credentials(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    monkeypatch.setattr(tracing, "_provider", None)

    def boom(endpoint: str) -> None:
        raise RuntimeError("no ADC")

    monkeypatch.setattr(tracing, "build_otlp_exporter", boom)
    with caplog.at_level(logging.WARNING):
        assert setup_tracing(None, _settings()) is None
    assert "tracing_disabled reason=exporter_unavailable" in caplog.text
    assert tracing._provider is None


def test_settings_defaults_are_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "TRACING_ENABLED",
        "TRACE_SAMPLE_RATIO_TURNS",
        "TRACE_SAMPLE_RATIO_HEALTH",
        "TRACE_CAPTURE_CONTENT",
    ):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.tracing_enabled is True and s.trace_sample_ratio_turns == 1.0
    assert s.trace_sample_ratio_health == 0.01 and s.trace_capture_content is False
    assert s.trace_otlp_endpoint == "https://telemetry.googleapis.com/v1/traces"


def test_resource_and_console_url() -> None:
    res = build_resource(
        project="top-skill",
        version="abc",
        env="prod",
        environ={"K_SERVICE": "topskill-api", "K_REVISION": "r1"},
    )
    attrs = dict(res.attributes)
    assert attrs["service.name"] == "topskill-api" and attrs["gcp.project_id"] == "top-skill"
    assert attrs["cloud.platform"] == "gcp_cloud_run" and attrs["faas.version"] == "r1"
    assert "cloud.platform" not in dict(
        build_resource(project="p", version="v", env="dev", environ={}).attributes
    )
    assert console_url("ab" * 16) == (
        "https://console.cloud.google.com/traces/list?project=top-skill&tid=" + "ab" * 16
    )


def test_fail_open_exporter() -> None:
    class Broken:
        calls = 0

        def export(self, spans):
            Broken.calls += 1
            if Broken.calls == 1:
                raise ConnectionError("403 PERMISSION_DENIED")
            return SpanExportResult.FAILURE

        def shutdown(self):
            raise RuntimeError("x")

        def force_flush(self, timeout_millis=0):
            raise RuntimeError("x")

    exp = FailOpenExporter(Broken(), interval_s=3600)  # type: ignore[arg-type]
    assert exp.export([]) == SpanExportResult.FAILURE
    assert exp.export([]) == SpanExportResult.FAILURE
    assert exp.failures == 2
    exp.shutdown()
    assert exp.force_flush() is False


def test_route_template() -> None:
    route = SimpleNamespace(path="/api/public/agents/{slug}/chat")
    assert (
        route_template({"path": "/api/public/agents/visa/chat", "route": route})
        == "/api/public/agents/{slug}/chat"
    )
    # a router-relative template is rebuilt from the path params
    rel = SimpleNamespace(path="/agents/{slug}/chat")
    scope = {"path": "/api/public/agents/visa/chat", "route": rel, "path_params": {"slug": "visa"}}
    assert route_template(scope) == "/api/public/agents/{slug}/chat"
    assert route_template({"path": "/nothing"}) is None


# --------------------------------------------------------------------------- turn aggregation


def _turn() -> tuple[TurnTrace, TracerProvider, list]:
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    span = provider.get_tracer("t").start_span("turn")
    return TurnTrace(span=span, channel="web"), provider, exporter


def test_turn_trace_from_frames() -> None:
    turn, _, exporter = _turn()
    emitted: list = []
    emit = turn.wrap(emitted.append)
    frames = [
        (
            EventName.STATUS,
            StatusData(conversation_id="cnv_1", turn_id="t1", request_id="turn_1", model="m-cfg"),
        ),
        (
            EventName.TOOL_START,
            ToolStartData(id="c1", tool="search_knowledge", kind="search", title="", target=""),
        ),
        (EventName.TOOL_END, ToolEndData(id="c1", tool="search_knowledge", status="completed")),
        (EventName.TEXT_DELTA, TextDeltaData(delta="Hi")),
        (EventName.TEXT_DELTA, TextDeltaData(delta=" there")),
        (
            EventName.TOOL_START,
            ToolStartData(id="web-0", tool="google_search", kind="web", title="", target=""),
        ),
        (
            EventName.TOOL_START,
            ToolStartData(id="web-1", tool="google_search", kind="web", title="", target=""),
        ),
        (
            EventName.TOOL_START,
            ToolStartData(id="r1", tool="save_record", kind="record", title="", target=""),
        ),
        (EventName.CITATIONS, CitationsData(sources=[], search_entry_point_html=None, web_search_queries=[])),
        (
            EventName.DONE,
            DoneData(
                conversation_id="cnv_1",
                message_id="m_1",
                turn_id="t1",
                outcome="ok",
                usage=DoneUsage(request_id="turn_1", input_tokens=120, output_tokens=30, model="m-real"),
                billing=DoneBilling(
                    charged_cents=100, free=False, round_index=3, free_rounds_left=0, balance_cents=900
                ),
            ),
        ),
    ]
    for f in frames:
        emit(f)
    assert emitted == frames  # frames pass through untouched
    turn.finish()
    turn.span.end()
    span = exporter.get_finished_spans()[0]
    a = dict(span.attributes)
    assert a["topskill.model"] == "m-real" and a["gen_ai.usage.input_tokens"] == 120
    assert a["gen_ai.usage.output_tokens"] == 30 and a["topskill.charged_cents"] == 100
    assert a["topskill.knowledge_searches"] == 1 and a["topskill.web_searches"] == 2
    assert a["topskill.tool_calls"] == 4 and a["topskill.outcome"] == "ok"
    assert a["topskill.conversation_id"] == "cnv_1" and a["topskill.request_id"] == "turn_1"
    assert "topskill.ttft_ms" in a and span.status.status_code.name == "OK"


def test_turn_trace_failed_and_aborted() -> None:
    turn, _, exporter = _turn()
    turn.on_frame(EventName.ERROR, StreamErrorData(code="turn_timeout", message="", trace_id=""))
    turn.on_frame(
        EventName.DONE,
        DoneData(
            conversation_id=None, message_id=None, turn_id="t", outcome="failed", usage=None, billing=None
        ),
    )
    turn.finish()
    turn.span.end()
    span = exporter.get_finished_spans()[0]
    assert span.attributes["topskill.error_code"] == "turn_timeout"
    assert span.attributes["topskill.charged_cents"] == 0 and span.status.status_code.name == "ERROR"

    import asyncio

    turn2, _, exporter2 = _turn()
    turn2.finish(asyncio.CancelledError())
    turn2.span.end()
    assert exporter2.get_finished_spans()[0].attributes["topskill.outcome"] == "aborted"
    turn3, _, exporter3 = _turn()
    turn3.on_frame("text_delta", None)  # malformed payloads never raise
    turn3.wrap(lambda f: None)("not-a-frame")
    turn3.finish(ValueError("boom"))
    turn3.span.end()
    s3 = exporter3.get_finished_spans()[0]
    assert s3.attributes["topskill.outcome"] == "failed" and s3.events[-1].name == "exception"


def _ev(**kw) -> Event:
    return Event(author=kw.pop("author", "agent"), invocation_id="i1", **kw)


def test_turn_trace_from_adk_events() -> None:
    turn, _, exporter = _turn()
    turn.on_adk_event(_ev(author="user", content=types.Content(role="user", parts=[types.Part(text="hi")])))
    assert turn.ttft_ms is None  # the user's own message is not a first token
    call = types.Part(function_call=types.FunctionCall(id="f1", name="search_knowledge", args={"query": "q"}))
    turn.on_adk_event(_ev(content=types.Content(role="model", parts=[call]), partial=True))
    turn.on_adk_event(_ev(content=types.Content(role="model", parts=[call])))
    turn.on_adk_event(_ev(content=types.Content(role="model", parts=[call])))  # same id, counted once
    turn.on_adk_event(
        _ev(
            content=types.Content(role="model", parts=[types.Part(text="Answer")]),
            model_version="gemini-3.8-flash",
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=50, candidates_token_count=7
            ),
            grounding_metadata=types.GroundingMetadata(web_search_queries=["a", "b", "a"]),
        )
    )
    turn.on_adk_event(_ev(error_code="SAFETY"))
    record_a2a_charge(SimpleNamespace(status="completed", charged_cents=200))  # no current turn: no-op
    turn.on_a2a_charge(SimpleNamespace(status="completed", charged_cents=200))
    turn.on_a2a_charge(SimpleNamespace(status="duplicate", charged_cents=200))
    turn.finish()
    turn.span.end()
    a = dict(exporter.get_finished_spans()[0].attributes)
    assert a["topskill.knowledge_searches"] == 1 and a["topskill.web_searches"] == 2
    assert a["gen_ai.usage.input_tokens"] == 50 and a["topskill.model"] == "gemini-3.8-flash"
    assert a["topskill.outcome"] == "ok" and a["topskill.charged_cents"] == 200
    assert a["topskill.error_code"] == "SAFETY" and "topskill.ttft_ms" in a


def test_a2a_call_span_reads_route_attributes(otel) -> None:
    def call() -> None:
        note_a2a_call(agent_id="agt_1", slug="visa", call_id="c1", price_cents=200, method="SendMessage")
        with a2a_call_span("task-1") as turn:
            record_a2a_charge(SimpleNamespace(status="failed", charged_cents=0))
            assert turn.outcome == "failed"

    contextvars.copy_context().run(call)  # the route's context var must not leak into other tests
    span = otel.one("topskill.a2a.call")
    assert span.attributes["topskill.slug"] == "visa" and span.attributes["topskill.task_id"] == "task-1"
    assert span.attributes["topskill.price_cents"] == 200 and "topskill.payer_uid" not in span.attributes


# --------------------------------------------------------------------------- logs


def test_json_log_line_carries_trace_and_span(otel) -> None:
    formatter = _JsonFormatter("top-skill")
    flt = _TraceIdFilter()
    with trace.get_tracer("t").start_as_current_span("POST /a2a/x") as span:
        ctx = span.get_span_context()
        trace_hex = format(ctx.trace_id, "032x")
        token = set_trace_id(trace_hex)
        try:
            record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("w",), None)
            flt.filter(record)
            line = json.loads(formatter.format(record))
        finally:
            reset_trace_id(token)
    assert line["message"] == "hello w" and line["severity"] == "INFO"
    assert line["logging.googleapis.com/trace"] == f"projects/top-skill/traces/{trace_hex}"
    assert line["logging.googleapis.com/spanId"] == format(ctx.span_id, "016x")
    assert line["logging.googleapis.com/trace_sampled"] is True

    # Outside a span: trace only (the request's trace id), no span id.
    token = set_trace_id("f" * 32)
    try:
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "m", (), None)
        flt.filter(record)
        line = json.loads(formatter.format(record))
    finally:
        reset_trace_id(token)
    assert line["trace_id"] == "f" * 32 and "logging.googleapis.com/spanId" not in line


def test_sampling_decision_type() -> None:
    result = _sampler(turn=0.0).should_sample(None, TRACE, "POST", attributes={"http.target": "/a2a/x"})
    assert result.decision == Decision.DROP


def test_setup_installs_provider_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    installed: list = []
    httpx_calls: list = []
    monkeypatch.setattr(tracing, "_provider", None)
    monkeypatch.setattr(tracing.trace, "set_tracer_provider", installed.append)
    monkeypatch.setattr(tracing, "_instrument_httpx", httpx_calls.append)
    monkeypatch.delenv("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", raising=False)
    exporter = InMemorySpanExporter()
    app = FastAPI()
    provider = setup_tracing(app, _settings(trace_sample_ratio_health=0.5), exporter=exporter, environ={})
    assert provider is not None and installed == [provider] and httpx_calls == [provider]
    assert tracing.tracing_active()
    assert "health=0.5" in provider.sampler.get_description()
    import os

    assert os.environ["ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"] == "false"
    # idempotent: a second app is instrumented with the same provider, nothing is re-installed
    assert setup_tracing(FastAPI(), _settings(), exporter=exporter) is provider and len(installed) == 1
    with provider.get_tracer("t").start_as_current_span("POST /a2a/x", attributes={"http.target": "/a2a/x"}):
        pass
    assert tracing.force_flush()
    assert [s.name for s in exporter.get_finished_spans()] == ["POST /a2a/x"]
    provider.shutdown()
