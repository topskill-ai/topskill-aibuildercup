"""One parent span per chat turn / A2A call (SPEC §18.3).

* Web chat: :func:`chat_turn_span` wraps the turn producer and observes the SSE frames it emits
  (``status`` → ``text_delta`` → ``tool_*`` → ``citations`` → ``error`` → ``done``), so the chat runtime
  needs no tracing code of its own. ADK's spans run inside it and become its children.
* A2A: the route records the call (:func:`note_a2a_call`), the executor runs inside
  :func:`a2a_call_span`, feeds every ADK event to :meth:`TurnTrace.on_adk_event` and the settlement
  result to :func:`record_a2a_charge`.

Span attributes (all ``topskill.*`` plus the GenAI usage conventions):
``agent_id``, ``slug``, ``channel`` (web | a2a), ``model``, ``gen_ai.usage.input_tokens`` /
``output_tokens``, ``outcome`` (ok | failed | aborted), ``charged_cents``, ``knowledge_searches``,
``web_searches``, ``ttft_ms`` (latency to the first answer token), ``error_code`` and ids
(``conversation_id``, ``request_id``, ``turn_id`` / ``call_id``). No message text is recorded.

Everything here is a no-op cost when tracing is off (the OTel API hands out non-recording spans) and
never raises into the caller.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

log = logging.getLogger(__name__)

TRACER_NAME = "topskill"
CHAT_TURN_SPAN = "topskill.chat.turn"
A2A_CALL_SPAN = "topskill.a2a.call"
KNOWLEDGE_TOOL = "search_knowledge"
WEB_SEARCH_AGENT_TOOL = "google_search_agent"

A = "topskill."  # attribute prefix

_current: contextvars.ContextVar[TurnTrace | None] = contextvars.ContextVar(
    "topskill_turn_trace", default=None
)
_a2a_call: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "topskill_a2a_call_attrs", default=None
)


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


def _get(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


@dataclass
class TurnTrace:
    """Accumulates one turn's facts and writes them onto ``span`` when the turn ends."""

    span: Span
    channel: str
    started: float = field(default_factory=time.monotonic)
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    outcome: str | None = None
    charged_cents: int | None = None
    error_code: str | None = None
    knowledge_searches: int = 0
    web_searches: int = 0
    tool_calls: int = 0
    ttft_ms: float | None = None
    _seen_calls: set[str] = field(default_factory=set)
    _seen_queries: set[str] = field(default_factory=set)
    _search_agent_used: bool = False

    # ---- shared helpers

    def _first_token(self) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = round((time.monotonic() - self.started) * 1000, 1)
            self.span.add_event("first_token", {A + "ttft_ms": self.ttft_ms})

    def _tool(self, call_id: str, name: str, kind: str | None) -> None:
        if call_id in self._seen_calls:
            return
        self._seen_calls.add(call_id)
        self.tool_calls += 1
        if kind == "search" or name == KNOWLEDGE_TOOL:
            self.knowledge_searches += 1
        elif kind == "web" or name in ("google_search", WEB_SEARCH_AGENT_TOOL):
            self.web_searches += 1

    # ---- web chat: SSE frames

    def on_frame(self, name: str, data: Any) -> None:
        try:
            if name == "status":
                self.model = self.model or _get(data, "model")
                for key in ("conversation_id", "turn_id", "request_id"):
                    value = _get(data, key)
                    if value:
                        self.span.set_attribute(A + key, str(value))
            elif name == "text_delta":
                if _get(data, "delta"):
                    self._first_token()
            elif name == "tool_start":
                self._tool(str(_get(data, "id")), str(_get(data, "tool")), _get(data, "kind"))
            elif name == "error":
                self.error_code = _get(data, "code")
            elif name == "done":
                self.outcome = _get(data, "outcome")
                usage = _get(data, "usage")
                if usage is not None:
                    self.input_tokens = _get(usage, "input_tokens")
                    self.output_tokens = _get(usage, "output_tokens")
                    self.model = _get(usage, "model") or self.model
                billing = _get(data, "billing")
                self.charged_cents = int(_get(billing, "charged_cents") or 0) if billing is not None else 0
                conversation_id = _get(data, "conversation_id")
                if conversation_id:
                    self.span.set_attribute(A + "conversation_id", str(conversation_id))
        except Exception:  # observability never breaks a turn
            log.debug("turn_trace_frame_failed name=%s", name, exc_info=True)

    def wrap(self, emit: Callable[[Any], None]) -> Callable[[Any], None]:
        """``emit`` that also observes each ``(event_name, payload)`` frame."""

        def traced_emit(frame: Any) -> None:
            try:
                name, data = frame
            except (TypeError, ValueError):
                name, data = None, None
            if name is not None:
                self.on_frame(name, data)
            emit(frame)

        return traced_emit

    # ---- A2A: ADK events

    def on_adk_event(self, ev: Any) -> None:
        try:
            model = _get(ev, "model_version")
            if isinstance(model, str) and model:
                self.model = model
            usage = _get(ev, "usage_metadata")
            if usage is not None:
                self.input_tokens = _get(usage, "prompt_token_count")
                self.output_tokens = _get(usage, "candidates_token_count")
            content = _get(ev, "content")
            if content is not None and _get(ev, "author") != "user":
                for part in _get(content, "parts") or []:
                    if _get(part, "text") and not _get(part, "thought"):
                        self._first_token()
                        break
            if not _get(ev, "partial"):
                calls = ev.get_function_calls() if hasattr(ev, "get_function_calls") else []
                for i, fc in enumerate(calls or []):
                    name = _get(fc, "name") or "tool"
                    self._tool(_get(fc, "id") or f"{name}-{len(self._seen_calls)}-{i}", name, None)
                    if name == WEB_SEARCH_AGENT_TOOL:
                        self._search_agent_used = True
            gm = _get(ev, "grounding_metadata")
            queries = (_get(gm, "web_search_queries") or _get(gm, "webSearchQueries")) if gm else None
            if queries and not self._search_agent_used:
                for q in queries:
                    if isinstance(q, str) and q not in self._seen_queries:
                        self._seen_queries.add(q)
                        self.web_searches += 1
                        self.tool_calls += 1
            code = _get(ev, "error_code")
            if code and code != "STOP":
                self.error_code = str(code)
        except Exception:
            log.debug("turn_trace_adk_event_failed", exc_info=True)

    def on_a2a_charge(self, charge: Any) -> None:
        try:
            status = _get(charge, "status")
            charged = int(_get(charge, "charged_cents") or 0)
            if status == "duplicate":
                self.charged_cents = charged if self.charged_cents is None else self.charged_cents
                return
            self.charged_cents = charged
            self.outcome = "ok" if status == "completed" else "failed"
        except Exception:
            log.debug("turn_trace_charge_failed", exc_info=True)

    # ---- end

    def finish(self, exc: BaseException | None = None) -> None:
        span = self.span
        try:
            if exc is not None and self.outcome is None:
                # A cancellation (BaseException) is an interrupted turn; any other exception a failure.
                self.outcome = "failed" if isinstance(exc, Exception) else "aborted"
            attrs: dict[str, Any] = {
                A + "channel": self.channel,
                A + "outcome": self.outcome or "unknown",
                A + "charged_cents": self.charged_cents or 0,
                A + "knowledge_searches": self.knowledge_searches,
                A + "web_searches": self.web_searches,
                A + "tool_calls": self.tool_calls,
                A + "latency_ms": round((time.monotonic() - self.started) * 1000, 1),
            }
            if self.model:
                attrs[A + "model"] = self.model
                attrs["gen_ai.response.model"] = self.model
            if self.input_tokens is not None:
                attrs["gen_ai.usage.input_tokens"] = int(self.input_tokens)
            if self.output_tokens is not None:
                attrs["gen_ai.usage.output_tokens"] = int(self.output_tokens)
            if self.ttft_ms is not None:
                attrs[A + "ttft_ms"] = self.ttft_ms
            if self.error_code:
                attrs[A + "error_code"] = self.error_code
            span.set_attributes(attrs)
            if exc is not None and isinstance(exc, Exception):
                span.record_exception(exc)
            if self.outcome == "failed":
                span.set_status(Status(StatusCode.ERROR, self.error_code or "failed"))
            elif self.outcome == "ok":
                span.set_status(Status(StatusCode.OK))
        except Exception:
            log.debug("turn_trace_finish_failed", exc_info=True)


def current_turn() -> TurnTrace | None:
    """The turn being traced in this context (``None`` outside a traced turn)."""
    return _current.get()


@contextlib.contextmanager
def _turn_span(name: str, channel: str, attributes: dict[str, Any]) -> Iterator[TurnTrace]:
    attrs = {A + "channel": channel, **{k: v for k, v in attributes.items() if v is not None}}
    with tracer().start_as_current_span(
        name,
        kind=trace.SpanKind.INTERNAL,
        attributes=attrs,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        turn = TurnTrace(span=span, channel=channel)
        token = _current.set(turn)
        try:
            yield turn
        except BaseException as exc:
            turn.finish(exc)
            raise
        else:
            turn.finish()
        finally:
            _current.reset(token)


def chat_turn_span(ctx: Any) -> contextlib.AbstractContextManager[TurnTrace]:
    """Parent span of one web chat turn; ``ctx`` is a ``chat.runtime.TurnContext``."""
    agent, version = ctx.agent, ctx.version
    return _turn_span(
        CHAT_TURN_SPAN,
        "web",
        {
            A + "agent_id": agent.id,
            A + "slug": agent.slug,
            A + "agent_version": getattr(version, "n", None),
            A + "model": getattr(version, "model", None),
            A + "conversation_id": ctx.conversation_id,
            A + "request_id": ctx.request_id,
            A + "turn_id": ctx.turn_id,
            A + "conversation_new": bool(ctx.conversation_is_new),
        },
    )


def note_a2a_call(
    *, agent_id: str, slug: str, call_id: str, price_cents: int, method: str, payer_uid: str | None = None
) -> None:
    """Called by the A2A route after the call was reserved; the executor's span picks these up."""
    _a2a_call.set(
        {
            A + "agent_id": agent_id,
            A + "slug": slug,
            A + "call_id": call_id,
            A + "price_cents": int(price_cents),
            A + "a2a_method": method,
            A + "payer_uid": payer_uid,
        }
    )


def a2a_call_span(task_id: str | None = None) -> contextlib.AbstractContextManager[TurnTrace]:
    """Parent span of one A2A call's execution (inside ``TopSkillA2aExecutor.execute``)."""
    attrs = dict(_a2a_call.get() or {})
    attrs[A + "task_id"] = task_id
    return _turn_span(A2A_CALL_SPAN, "a2a", attrs)


def record_a2a_charge(charge: Any) -> None:
    turn = current_turn()
    if turn is not None and charge is not None:
        turn.on_a2a_charge(charge)
