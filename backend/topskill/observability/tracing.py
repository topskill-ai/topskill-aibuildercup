"""OpenTelemetry → Cloud Trace (SPEC §18.3).

:func:`setup_tracing` installs one global ``TracerProvider`` whose spans go to Cloud Trace through the
Telemetry API's OTLP endpoint (``https://telemetry.googleapis.com/v1/traces``, OTLP/HTTP protobuf,
authenticated with ADC). It instruments FastAPI (server spans) and httpx (client spans, which also
carry the Gemini calls of google-genai and the Enterprise Assistant's A2A calls with W3C
``traceparent``), and ADK's own spans (``invoke_agent``, ``call_llm``, ``execute_tool``) join the same
trace because ADK uses the global provider.

Fail-open: when tracing is disabled, ADC cannot be loaded or the exporter cannot be built, the app runs
untraced (a warning is logged once). Export runs in the ``BatchSpanProcessor`` worker thread, never on a
request path; export failures (e.g. a missing IAM role) are logged at most once per
``_FAILURE_LOG_INTERVAL_S``.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import Decision, Sampler, SamplingResult, TraceIdRatioBased
from opentelemetry.trace import Link, SpanKind
from opentelemetry.util.types import Attributes

log = logging.getLogger(__name__)

SERVICE_NAME = "topskill-api"
TELEMETRY_TRACES_ENDPOINT = "https://telemetry.googleapis.com/v1/traces"
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
CONSOLE_TRACE_URL = "https://console.cloud.google.com/traces/list?project={project}&tid={trace_id}"
_FAILURE_LOG_INTERVAL_S = 300.0

# Routes whose every request is traced (a chat turn, an A2A call, the assistant, voice/live sessions).
TURN_PATH_RE = re.compile(
    r"^/(?:api/public/agents/[^/]+/(?:chat|voice|live)\b|a2a/[^/]+/?$|api/demo/assistant/|.*/(?:voice|live)\b)"
)
# Liveness/meta probes and CORS preflights: a small sample only.
HEALTH_PATHS = frozenset({"/health", "/healthz", "/api/public/meta"})

_state_lock = threading.Lock()
_provider: TracerProvider | None = None
_instrumented_app_ids: set[int] = set()


# --------------------------------------------------------------------------- sampling


def _path_of(attributes: Attributes, name: str) -> str | None:
    if attributes:
        for key in ("url.path", "http.target", "http.route"):
            value = attributes.get(key)
            if isinstance(value, str) and value:
                return value.split("?", 1)[0]
    # FastAPI server span names are "METHOD /route/{template}".
    method, _, rest = name.partition(" ")
    if rest.startswith("/") and method.isupper():
        return rest
    return None


def _method_of(attributes: Attributes, name: str) -> str | None:
    if attributes:
        for key in ("http.request.method", "http.method"):
            value = attributes.get(key)
            if isinstance(value, str) and value:
                return value.upper()
    method, _, rest = name.partition(" ")
    return method if rest.startswith("/") and method.isupper() else None


class RouteSampler(Sampler):
    """Head sampling by route for root spans and remote parents; local children follow their parent.

    * turn routes (:data:`TURN_PATH_RE`) → ``turn_ratio`` (1.0 in prod: every chat turn / A2A call),
    * ``/health``, ``/healthz``, ``/api/public/meta`` and ``OPTIONS`` → ``health_ratio``,
    * anything else (other API routes, background work started outside a request) → ``default_ratio``.

    A remote parent that is already sampled (e.g. the Enterprise Assistant calling ``/a2a/{slug}`` with a
    sampled ``traceparent``, or a ``X-Cloud-Trace-Context: …;o=1`` forced trace) is always honoured, so a
    caller's trace is never cut in half. An unsampled remote parent does NOT force a drop: Cloud Run's
    front end sends ``o=0`` for most requests and we still want every turn traced.
    """

    def __init__(self, *, turn_ratio: float, default_ratio: float, health_ratio: float) -> None:
        self._turn = TraceIdRatioBased(turn_ratio)
        self._default = TraceIdRatioBased(default_ratio)
        self._health = TraceIdRatioBased(health_ratio)

    def rule_for(self, name: str, attributes: Attributes) -> TraceIdRatioBased:
        path = _path_of(attributes, name)
        if _method_of(attributes, name) == "OPTIONS":
            return self._health
        if path is None:
            return self._default
        if path in HEALTH_PATHS:
            return self._health
        if TURN_PATH_RE.match(path):
            return self._turn
        return self._default

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: Any = None,
    ) -> SamplingResult:
        parent = trace.get_current_span(parent_context).get_span_context()
        parent_state = parent.trace_state if parent.is_valid else None
        if parent.is_valid and (not parent.is_remote or parent.trace_flags.sampled):
            decision = Decision.RECORD_AND_SAMPLE if parent.trace_flags.sampled else Decision.DROP
            return SamplingResult(decision, attributes if decision.is_sampled() else None, parent_state)
        rule = self.rule_for(name, attributes)
        result = rule.should_sample(parent_context, trace_id, name, kind, attributes, links)
        return SamplingResult(result.decision, result.attributes, parent_state)

    def get_description(self) -> str:
        return (
            f"RouteSampler(turn={self._turn.rate}, default={self._default.rate}, health={self._health.rate})"
        )


# --------------------------------------------------------------------------- exporter


class FailOpenExporter(SpanExporter):
    """Wraps the real exporter: never raises, logs failures at most once per interval."""

    def __init__(self, inner: SpanExporter, *, interval_s: float = _FAILURE_LOG_INTERVAL_S) -> None:
        self._inner = inner
        self._interval = interval_s
        self._last_failure_log = 0.0
        self.failures = 0
        self.exported = 0

    def _note_failure(self, detail: str) -> None:
        self.failures += 1
        now = time.monotonic()
        if now - self._last_failure_log >= self._interval:
            self._last_failure_log = now
            log.warning("trace_export_failed failures=%d detail=%s", self.failures, detail)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._inner.export(spans)
        except Exception as exc:
            self._note_failure(repr(exc)[:300])
            return SpanExportResult.FAILURE
        if result == SpanExportResult.SUCCESS:
            self.exported += len(spans)
        else:
            self._note_failure("exporter returned FAILURE")
        return result

    def shutdown(self) -> None:
        try:
            self._inner.shutdown()
        except Exception:
            log.debug("trace_exporter_shutdown_failed", exc_info=True)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return bool(self._inner.force_flush(timeout_millis))
        except Exception:
            return False


class _RateLimitFilter(logging.Filter):
    """Lets one record through per interval (the OTLP exporter logs every failed batch itself)."""

    def __init__(self, interval_s: float = _FAILURE_LOG_INTERVAL_S) -> None:
        super().__init__()
        self._interval = interval_s
        self._last = 0.0

    def filter(self, record: logging.LogRecord) -> bool:
        now = time.monotonic()
        if now - self._last >= self._interval:
            self._last = now
            return True
        return False


def build_otlp_exporter(
    endpoint: str = TELEMETRY_TRACES_ENDPOINT, *, timeout_s: float = 10.0
) -> SpanExporter:
    """OTLP/HTTP exporter to the Telemetry API with ADC (metadata server on Cloud Run, user ADC locally;
    ``requests`` honours ``HTTPS_PROXY``)."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
    return OTLPSpanExporter(endpoint=endpoint, session=AuthorizedSession(credentials), timeout=timeout_s)


# --------------------------------------------------------------------------- setup


def build_resource(*, project: str, version: str, env: str, environ: Mapping[str, str]) -> Resource:
    attrs: dict[str, Any] = {
        "service.name": SERVICE_NAME,
        "service.version": version,
        "deployment.environment.name": env,
        "gcp.project_id": project,  # required by the Telemetry API to route spans to the project
        "cloud.provider": "gcp",
    }
    if environ.get("K_SERVICE"):
        attrs.update(
            {
                "cloud.platform": "gcp_cloud_run",
                "faas.name": environ["K_SERVICE"],
                "faas.version": environ.get("K_REVISION", ""),
            }
        )
    return Resource.create(attrs)


def install_propagators() -> None:
    """W3C ``traceparent`` + baggage + Google's ``X-Cloud-Trace-Context`` (extract both, inject both)."""
    from opentelemetry import propagate
    from opentelemetry.baggage.propagation import W3CBaggagePropagator
    from opentelemetry.propagators.cloud_trace_propagator import CloudTraceFormatPropagator
    from opentelemetry.propagators.composite import CompositePropagator
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    propagate.set_global_textmap(
        CompositePropagator(
            [CloudTraceFormatPropagator(), TraceContextTextMapPropagator(), W3CBaggagePropagator()]
        )
    )


def make_provider(
    settings: Any, *, exporter: SpanExporter | None, environ: Mapping[str, str]
) -> TracerProvider:
    provider = TracerProvider(
        resource=build_resource(
            project=settings.google_cloud_project,
            version=settings.app_version,
            env=settings.env,
            environ=environ,
        ),
        sampler=RouteSampler(
            turn_ratio=settings.trace_sample_ratio_turns,
            default_ratio=settings.trace_sample_ratio,
            health_ratio=settings.trace_sample_ratio_health,
        ),
    )
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(FailOpenExporter(exporter)))
    return provider


def setup_tracing(
    app: Any = None,
    settings: Any = None,
    *,
    exporter: SpanExporter | None = None,
    environ: Mapping[str, str] | None = None,
) -> TracerProvider | None:
    """Install the global provider once (idempotent) and instrument ``app`` + httpx.

    Returns the provider, or ``None`` when tracing is off or could not be set up (fail-open)."""
    global _provider
    if settings is None:
        from topskill.config import get_settings

        settings = get_settings()
    environ = os.environ if environ is None else environ
    if not settings.tracing_enabled:
        return None
    with _state_lock:
        if _provider is None:
            # ADK puts full prompts and responses into its spans unless told otherwise (visitor PII).
            os.environ.setdefault(
                "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "true" if settings.trace_capture_content else "false"
            )
            if exporter is None:
                try:
                    exporter = build_otlp_exporter(settings.trace_otlp_endpoint)
                except Exception as exc:
                    log.warning("tracing_disabled reason=exporter_unavailable error=%r", exc)
                    return None
            try:
                provider = make_provider(settings, exporter=exporter, environ=environ)
                install_propagators()
                trace.set_tracer_provider(provider)
                logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter").addFilter(
                    _RateLimitFilter()
                )
                _instrument_httpx(provider)
            except Exception as exc:
                log.warning("tracing_disabled reason=setup_failed error=%r", exc)
                return None
            _provider = provider
            log.info(
                "tracing_enabled endpoint=%s sampler=%s",
                settings.trace_otlp_endpoint,
                provider.sampler.get_description(),
            )
        provider = _provider
    if app is not None:
        instrument_app(app, provider)
    return provider


_PARAM_RE = re.compile(r"\{([^}:]+)(?::[^}]+)?\}")


def route_template(scope: Mapping[str, Any]) -> str | None:
    """The matched route template (``/api/public/agents/{slug}/chat``) of a routed request.

    FastAPI ≥ 0.141 wraps included routers, so the OTel FastAPI instrumentation cannot resolve the template
    at span start and names server spans after the raw path (one span name per slug). After routing,
    ``scope["route"]`` / ``scope["path_params"]`` are known; a template that does not match the full path
    (a router-relative path) is rebuilt from the path params instead."""
    path = scope.get("path")
    if not isinstance(path, str):
        return None
    route = scope.get("route")
    candidate = getattr(route, "path_format", None) or getattr(route, "path", None)
    if isinstance(candidate, str) and candidate:
        pattern = (
            "^" + _PARAM_RE.sub("[^/]+", re.escape(candidate).replace(r"\{", "{").replace(r"\}", "}")) + "$"
        )
        if re.match(pattern, path):
            return candidate
    params = scope.get("path_params") or {}
    if route is None and not params:
        return None
    segments = path.split("/")
    for key, value in params.items():
        for i in range(len(segments) - 1, -1, -1):
            if segments[i] == str(value):
                segments[i] = "{" + key + "}"
                break
    return "/".join(segments)


class RouteNameMiddleware:
    """Renames the current server span to ``METHOD /route/{template}`` and sets ``http.route`` once the
    request was routed (runs inside the OTel middleware; pure ASGI, streaming-safe)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        span = trace.get_current_span()
        named = False

        def name_span() -> None:
            nonlocal named
            if named or not span.is_recording():
                return
            named = True
            template = route_template(scope)
            if template:
                span.update_name(f"{scope.get('method', 'HTTP')} {template}")
                span.set_attribute("http.route", template)

        async def send_wrapper(message: Any) -> None:
            if message.get("type") == "http.response.start":
                name_span()
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            name_span()


def instrument_app(app: Any, provider: TracerProvider) -> None:
    """FastAPI server spans (outermost middleware), without one span per ASGI send/receive (SSE)."""
    if id(app) in _instrumented_app_ids:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        app.add_middleware(RouteNameMiddleware)

        FastAPIInstrumentor.instrument_app(
            app, tracer_provider=provider, exclude_spans=["receive", "send"], excluded_urls=None
        )
        _instrumented_app_ids.add(id(app))
    except Exception as exc:
        log.warning("tracing_fastapi_instrumentation_failed error=%r", exc)


def _instrument_httpx(provider: TracerProvider) -> None:
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        instrumentor = HTTPXClientInstrumentor()
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument(tracer_provider=provider)
    except Exception as exc:
        log.warning("tracing_httpx_instrumentation_failed error=%r", exc)


def tracing_active() -> bool:
    return _provider is not None


def force_flush(timeout_millis: int = 10_000) -> bool:
    """Flush pending spans (shutdown, live tests)."""
    provider = _provider
    return bool(provider.force_flush(timeout_millis)) if provider is not None else True


def shutdown_tracing() -> None:
    """Flush and stop the exporter (lifespan shutdown). Safe to call when tracing is off."""
    provider = _provider
    if provider is not None:
        try:
            provider.shutdown()
        except Exception:
            log.debug("tracing_shutdown_failed", exc_info=True)


# --------------------------------------------------------------------------- ids


def current_otel_ids() -> tuple[str, str, bool] | None:
    """``(trace_id_hex32, span_id_hex16, sampled)`` of the current span, or ``None`` outside a span."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x"), bool(ctx.trace_flags.sampled)


def console_url(trace_id: str, project: str = "top-skill") -> str:
    """Cloud Trace explorer URL for one trace id (the ``trace_id`` of an error envelope or log line)."""
    return CONSOLE_TRACE_URL.format(trace_id=trace_id, project=project)
