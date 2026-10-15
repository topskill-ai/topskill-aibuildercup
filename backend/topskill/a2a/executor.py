"""Per-agent A2A server stack and the settling executor (SPEC §9.1).

Every reserved call (T8) ends ``completed`` or ``failed``: the executor settles on the normal path in
``after_agent`` (charged only on ``TASK_STATE_COMPLETED`` with a visible answer), its ``execute`` settles
any other exit (model/tool exception → ADK enqueues FAILED itself, timeout, cancellation) as failed, and
``cancel`` settles a cancelled task (charged once answer text was emitted; the route rejects
``CancelTask`` for paid calls anyway). ``billing.settle_a2a_call`` is idempotent, so double calls are no-ops.

Probe result (ADK 2.12, a2a-sdk 1.2.2, verified by ``tests/a2a``): the legacy ``A2aAgentExecutor.execute``
runs ``before_agent`` / ``after_event`` / ``after_agent`` inside the same task as ``execute`` itself, so a
``ContextVar`` set in ``execute`` is visible to every interceptor.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from a2a.auth.user import User
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCard,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.adk.a2a.converters.request_converter import (
    A2A_METADATA_KEY,
    AgentRunRequest,
    convert_a2a_request_to_agent_run_request,
)
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
from google.adk.a2a.executor.config import A2aAgentExecutorConfig, ExecuteInterceptor
from google.adk.a2a.executor.executor_context import ExecutorContext
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from starlette.requests import Request

from topskill import repo, tasks
from topskill.config import get_settings
from topskill.db import Col, get_db
from topskill.domain import AgentDoc, AgentVersion
from topskill.errors import ApiError, Codes
from topskill.observability import a2a_call_span, current_turn, record_a2a_charge

from . import _deps
from .cards import build_card

log = logging.getLogger(__name__)

APP_NAME = "topskill-a2a"
STACK_CACHE_MAX = 64
MANDATE_HEADER = "x-ap2-mandate"
MANDATE_METADATA_KEY = "ap2_mandate"
ANSWER_PREVIEW_CHARS = 300
CLIENT_MARGIN_MAX_S = 10.0
DEADLINE_STATE_KEY = "a2a_deadline"


def run_budget_s() -> float:
    """How long one paid A2A call may run on the server, counted from the route's entry.

    It always ends before the Enterprise Assistant's client gives up (``A2A_CALL_TIMEOUT_S``): a run that
    outlived the caller would still be charged after the caller reported it as failed and free. The margin
    (at most 10 s, a quarter of the client timeout for small values) leaves time to settle and answer."""
    s = get_settings()
    call_timeout = float(s.a2a_call_timeout_s)
    margin = min(CLIENT_MARGIN_MAX_S, call_timeout / 4)
    return max(0.5, min(float(s.turn_total_timeout_s), call_timeout - margin))


# --------------------------------------------------------------------------- call context


class PayerUser(User):
    """The authenticated A2A principal: ``a2a:<payer_uid>`` (ADK ``user_id``; task-store owner)."""

    def __init__(self, payer_uid: str) -> None:
        self._name = f"a2a:{payer_uid}"

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


class MandateContextBuilder(DefaultServerCallContextBuilder):
    """Copies ``request.state.verified_mandate`` / ``a2a_call_id`` into the call context, makes the payer
    the authenticated user and drops the mandate header from the copied headers (bearer token)."""

    def build(self, request: Request) -> ServerCallContext:
        ctx = super().build(request)
        headers = ctx.state.get("headers")
        if isinstance(headers, dict):
            headers.pop(MANDATE_HEADER, None)
            headers.pop("authorization", None)
        mandate = getattr(request.state, "verified_mandate", None)
        if mandate is not None:
            ctx.state["verified_mandate"] = mandate
            ctx.user = PayerUser(mandate.payer_uid)
        call_id = getattr(request.state, "a2a_call_id", None)
        if call_id:
            ctx.state["a2a_call_id"] = call_id
        deadline = getattr(request.state, DEADLINE_STATE_KEY, None)
        if isinstance(deadline, float):
            ctx.state[DEADLINE_STATE_KEY] = deadline
        return ctx


def limited_request_converter(request: RequestContext, part_converter: Any) -> AgentRunRequest:
    """ADK's default converter, plus ``max_llm_calls = MAX_LLM_CALLS_PER_TURN`` and without the mandate
    token in the metadata copied to ``RunConfig.custom_metadata`` (it would land in session events)."""
    run_request = convert_a2a_request_to_agent_run_request(request, part_converter)
    run_config = run_request.run_config or RunConfig()
    run_config.max_llm_calls = get_settings().max_llm_calls_per_turn
    metadata = dict(run_config.custom_metadata or {})
    a2a_metadata = metadata.get(A2A_METADATA_KEY)
    if isinstance(a2a_metadata, dict):
        a2a_metadata = {k: v for k, v in a2a_metadata.items() if k != MANDATE_METADATA_KEY}
        if a2a_metadata:
            metadata[A2A_METADATA_KEY] = a2a_metadata
        else:
            metadata.pop(A2A_METADATA_KEY)
    run_config.custom_metadata = metadata or None
    run_request.run_config = run_config
    return run_request


# --------------------------------------------------------------------------- settlement


async def settle_call(
    call_id: str, *, succeeded: bool, model: str | None, answer_preview: str, error_code: str | None = None
) -> Any:
    """``billing.settle_a2a_call`` through ``chat.get_deps()`` (idempotent T3)."""
    charge = await _deps.billing().settle_a2a_call(
        call_id=call_id,
        succeeded=succeeded,
        model=model,
        answer_preview=answer_preview[:ANSWER_PREVIEW_CHARS],
        error_code=error_code,
    )
    record_a2a_charge(charge)  # onto the call's span, when one is current
    return charge


async def _settle_safely(
    call_id: str,
    *,
    error_code: str | None,
    succeeded: bool = False,
    model: str | None = None,
    answer_preview: str = "",
) -> None:
    """Settle (failed and not charged by default); runs as a tracked task so a cancellation cannot
    interrupt it."""
    task = tasks.spawn(
        settle_call(
            call_id, succeeded=succeeded, model=model, answer_preview=answer_preview, error_code=error_code
        ),
        name=f"a2a-settle:{call_id}",
    )
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("a2a_settle_failed call_id=%s", call_id)


@dataclass
class CallState:
    call_id: str | None
    task_id: str | None
    answer_parts: list[str] = field(default_factory=list)
    model: str | None = None
    settled: bool = False


# Calls whose executor is still running, across every stack (a stack may be rebuilt mid-call).
_active_calls: set[str] = set()

_CALL: contextvars.ContextVar[CallState | None] = contextvars.ContextVar("topskill_a2a_call", default=None)


def _texts(parts: Any) -> list[str]:
    out: list[str] = []
    for part in parts or []:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            out.append(text)
    return out


def _event_texts(event: Any) -> list[str]:
    if isinstance(event, TaskArtifactUpdateEvent):
        return _texts(event.artifact.parts)
    if (
        isinstance(event, TaskStatusUpdateEvent)
        and event.HasField("status")
        and event.status.HasField("message")
    ):
        return _texts(event.status.message.parts)
    return []


async def _last_model_text(exec_ctx: ExecutorContext) -> str:
    """Fallback answer: the last non-user text in the ADK session."""
    try:
        session = await exec_ctx.runner.session_service.get_session(
            app_name=exec_ctx.app_name, user_id=exec_ctx.user_id, session_id=exec_ctx.session_id
        )
    except Exception:
        log.warning("a2a_session_read_failed", exc_info=True)
        return ""
    for ev in reversed(getattr(session, "events", None) or []):
        if ev.author == "user" or ev.content is None:
            continue
        text = "".join(p.text for p in ev.content.parts or [] if p.text and not p.thought)
        if text.strip():
            return text
    return ""


class TopSkillA2aExecutor(A2aAgentExecutor):
    """``A2aAgentExecutor`` that settles the reserved call on every exit path (SPEC §9.1)."""

    def __init__(self, *, runner: Runner) -> None:
        config = A2aAgentExecutorConfig(
            request_converter=limited_request_converter,
            execute_interceptors=[
                ExecuteInterceptor(
                    before_agent=self._before, after_event=self._after_event, after_agent=self._after
                )
            ],
        )
        # The legacy implementation is the one the interceptor contract above was verified against.
        super().__init__(runner=runner, config=config, use_legacy=True)
        self._call_for_task: dict[str, str] = {}
        self._state_for_task: dict[str, CallState] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        with a2a_call_span(context.task_id):  # P2 §18.3: parent span of the call (ADK spans nest)
            await self._execute(context, event_queue)

    async def _execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        call_ctx = context.call_context
        call_state = call_ctx.state if call_ctx is not None else {}
        call_id = call_state.get("a2a_call_id")
        deadline = call_state.get(DEADLINE_STATE_KEY)
        if not isinstance(deadline, float):
            deadline = asyncio.get_running_loop().time() + run_budget_s()
        state = CallState(call_id=call_id, task_id=context.task_id)
        token = _CALL.set(state)
        if call_id:
            _active_calls.add(call_id)
        try:
            async with asyncio.timeout_at(deadline):
                await super().execute(context, event_queue)
        except TimeoutError:
            log.warning("a2a_turn_timeout call_id=%s task_id=%s", call_id, context.task_id)
            if call_id and not state.settled:  # settle first: the caller reads the call doc on FAILED
                await _settle_safely(call_id, error_code=Codes.AGENT_RUN_FAILED)
                state.settled = True
            await self._enqueue_failed(context, event_queue, "The agent took too long to answer.")
        finally:
            _CALL.reset(token)
            try:
                if call_id and not state.settled:  # exception / timeout / FAILED path: never charged
                    await _settle_safely(call_id, error_code=Codes.AGENT_RUN_FAILED)
                    state.settled = True
            finally:
                if call_id:
                    _active_calls.discard(call_id)
                if context.task_id:
                    self._call_for_task.pop(context.task_id, None)
                    self._state_for_task.pop(context.task_id, None)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """The route rejects ``CancelTask`` for paid calls; this is the defence behind it. A cancel that
        arrives after answer text was emitted cannot make the answer free (the caller already has it,
        §7.4): such a call is settled as succeeded, anything earlier as failed (not charged)."""
        try:
            await super().cancel(context, event_queue)
        finally:
            task_id = context.task_id or ""
            call_id = self._call_for_task.get(task_id)
            st = self._state_for_task.get(task_id)
            if call_id and not (st is not None and st.settled):
                answer = "\n".join(p for p in st.answer_parts if p.strip()) if st is not None else ""
                if answer.strip():
                    model = st.model if st is not None else None
                    await _settle_safely(
                        call_id, error_code=None, succeeded=True, model=model, answer_preview=answer
                    )
                else:
                    await _settle_safely(call_id, error_code="canceled")
                if st is not None:
                    st.settled = True

    async def _enqueue_failed(self, context: RequestContext, event_queue: EventQueue, text: str) -> None:
        if not context.task_id or not context.context_id:
            return
        try:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(
                        state=TaskState.TASK_STATE_FAILED,
                        message=Message(
                            message_id=uuid.uuid4().hex, role=Role.ROLE_AGENT, parts=[Part(text=text)]
                        ),
                    ),
                )
            )
        except Exception:
            log.warning("a2a_enqueue_failed_event_failed task_id=%s", context.task_id, exc_info=True)

    # ---- interceptors (same task as execute → _CALL is visible)

    async def _before(self, context: RequestContext) -> RequestContext:
        st = _CALL.get()
        if st is not None and st.call_id and context.task_id:
            st.task_id = context.task_id
            self._call_for_task[context.task_id] = st.call_id
            self._state_for_task[context.task_id] = st
            try:
                await (
                    get_db()
                    .collection(Col.A2A_CALLS)
                    .document(st.call_id)
                    .update({"task_id": context.task_id})
                )
            except Exception:
                log.warning("a2a_call_task_id_update_failed call_id=%s", st.call_id, exc_info=True)
        return context

    async def _after_event(self, exec_ctx: ExecutorContext, event: Any, adk_event: Any) -> Any:
        turn_trace = current_turn()
        if turn_trace is not None:
            turn_trace.on_adk_event(adk_event)
        st = _CALL.get()
        if st is not None:
            texts = _event_texts(event)
            if texts:
                st.answer_parts.append("".join(texts))
            model = getattr(adk_event, "model_version", None)
            if isinstance(model, str) and model:
                st.model = model
        return event

    async def _after(
        self, exec_ctx: ExecutorContext, final_event: TaskStatusUpdateEvent
    ) -> TaskStatusUpdateEvent:
        st = _CALL.get()
        if st is None or not st.call_id or st.settled:
            return final_event
        try:
            completed = final_event.status.state == TaskState.TASK_STATE_COMPLETED
            answer = "\n".join(p for p in st.answer_parts if p.strip())
            if completed and not answer.strip():
                answer = await _last_model_text(exec_ctx)
            succeeded = completed and bool(answer.strip())
            await settle_call(
                st.call_id,
                succeeded=succeeded,
                model=st.model,
                answer_preview=answer,
                error_code=None if succeeded else Codes.AGENT_RUN_FAILED,
            )
            st.settled = True
        except Exception:
            log.exception("a2a_after_agent_settle_failed call_id=%s", st.call_id)
        return final_event


# --------------------------------------------------------------------------- stack cache


@dataclass
class A2AStack:
    key: tuple[str, int, str, int]  # (slug, active_version, updated_at iso, knowledge_rev)
    card: AgentCard
    dispatcher: JsonRpcDispatcher
    executor: TopSkillA2aExecutor


_stacks: OrderedDict[str, A2AStack] = OrderedDict()
_locks: dict[str, asyncio.Lock] = {}


def stack_key(agent: AgentDoc) -> tuple[str, int, str, int]:
    return (agent.slug, agent.active_version, agent.updated_at.isoformat(), agent.knowledge_rev)


def agent_offline(slug: str) -> ApiError:
    return ApiError(410, Codes.AGENT_OFFLINE, "This agent is temporarily unavailable.", {"slug": slug})


def agent_not_found() -> ApiError:
    return ApiError(404, Codes.AGENT_NOT_FOUND, "This agent does not exist.")


async def load_a2a_agent(slug: str) -> AgentDoc:
    """The agent behind ``/a2a/{slug}``: 404 unknown or A2A disabled; 410 paused/deleted."""
    agent = await repo.get_agent_by_slug(slug)
    if agent is None:
        raise agent_not_found()
    if agent.deleted or agent.status != "active":
        raise agent_offline(slug)
    if not agent.pricing.a2a_enabled:
        raise agent_not_found()
    return agent


async def build_stack(agent: AgentDoc, version: AgentVersion) -> A2AStack:
    llm_agent = await _deps.build_runtime_agent(agent, version)
    runner = Runner(app_name=APP_NAME, agent=llm_agent, session_service=InMemorySessionService())
    executor = TopSkillA2aExecutor(runner=runner)
    card = build_card(agent, version)
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)
    dispatcher = JsonRpcDispatcher(
        request_handler=handler, context_builder=MandateContextBuilder(), enable_v0_3_compat=True
    )
    return A2AStack(key=stack_key(agent), card=card, dispatcher=dispatcher, executor=executor)


async def get_stack(slug: str, agent: AgentDoc | None = None) -> A2AStack:
    """Cached stack for ``slug`` (LRU, max 64), rebuilt whenever ``stack_key`` changes."""
    agent = agent or await load_a2a_agent(slug)
    key = stack_key(agent)
    lock = _locks.setdefault(slug, asyncio.Lock())
    async with lock:
        stack = _stacks.get(slug)
        if stack is not None and stack.key == key:
            _stacks.move_to_end(slug)
            return stack
        version = await repo.get_active_version(agent)
        stack = await build_stack(agent, version)
        _stacks[slug] = stack
        _stacks.move_to_end(slug)
        while len(_stacks) > STACK_CACHE_MAX:
            evicted, _ = _stacks.popitem(last=False)
            _locks.pop(evicted, None)
        return stack


def is_call_active(call_id: str) -> bool:
    """True while an executor is still running this call (it will settle the call itself)."""
    return call_id in _active_calls


def clear_stacks() -> None:
    """Forget every cached stack (tests only)."""
    _stacks.clear()
    _locks.clear()
