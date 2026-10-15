from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from opentelemetry import propagate, trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tests.a2a.conftest import a2a_env  # noqa: F401 (shared a2a fixture)
from tests.chat.conftest import _fresh_fake_llm, doubles  # noqa: F401 (shared chat fixtures)
from topskill.observability import tracing
from topskill.observability.tracing import RouteSampler, install_propagators


class _GatedProcessor(SimpleSpanProcessor):
    """Records spans only while a test holds the ``otel`` fixture."""

    enabled = False

    def on_end(self, span: ReadableSpan) -> None:
        if self.enabled:
            super().on_end(span)


@dataclass
class Otel:
    provider: TracerProvider
    exporter: InMemorySpanExporter

    def spans(self) -> list[ReadableSpan]:
        return list(self.exporter.get_finished_spans())

    def by_name(self, name: str) -> list[ReadableSpan]:
        return [s for s in self.spans() if s.name == name]

    def one(self, name: str) -> ReadableSpan:
        found = self.by_name(name)
        assert len(found) == 1, (name, [s.name for s in self.spans()])
        return found[0]


_session: tuple[TracerProvider, InMemorySpanExporter, _GatedProcessor] | None = None


def _global_provider() -> tuple[TracerProvider, InMemorySpanExporter, _GatedProcessor]:
    """One process-wide provider (the OTel API allows a single global one, and ADK's module-level proxy
    tracer binds to the first one it sees), installed the first time a test asks for it."""
    global _session
    if _session is None:
        exporter = InMemorySpanExporter()
        provider = TracerProvider(sampler=RouteSampler(turn_ratio=1.0, default_ratio=1.0, health_ratio=0.0))
        processor = _GatedProcessor(exporter)
        provider.add_span_processor(processor)
        install_propagators()
        trace.set_tracer_provider(provider)
        _session = (provider, exporter, processor)
    return _session


@pytest.fixture
def otel(monkeypatch: pytest.MonkeyPatch) -> Iterator[Otel]:
    # What setup_tracing() does with TRACE_CAPTURE_CONTENT=false (the prod value).
    monkeypatch.setenv("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "false")
    provider, exporter, processor = _global_provider()
    old_textmap = propagate.get_global_textmap()
    install_propagators()
    exporter.clear()
    processor.enabled = True
    old_provider = tracing._provider
    tracing._provider = provider
    try:
        yield Otel(provider=provider, exporter=exporter)
    finally:
        processor.enabled = False
        exporter.clear()
        tracing._provider = old_provider
        propagate.set_global_textmap(old_textmap)
