"""Observability: OpenTelemetry tracing to Cloud Trace and per-turn spans (SPEC §18.3)."""

from topskill.observability.tracing import (
    console_url,
    current_otel_ids,
    force_flush,
    setup_tracing,
    shutdown_tracing,
    tracing_active,
)
from topskill.observability.turns import (
    TurnTrace,
    a2a_call_span,
    chat_turn_span,
    current_turn,
    note_a2a_call,
    record_a2a_charge,
)

__all__ = [
    "TurnTrace",
    "a2a_call_span",
    "chat_turn_span",
    "console_url",
    "current_otel_ids",
    "current_turn",
    "force_flush",
    "note_a2a_call",
    "record_a2a_charge",
    "setup_tracing",
    "shutdown_tracing",
    "tracing_active",
]
