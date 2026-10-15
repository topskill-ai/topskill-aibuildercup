"""Web chat turn runtime (SPEC §7, §10.4 "Turn algorithm").

Stateless: every turn builds an ADK ``LlmAgent`` from the active version, seeds an ``InMemorySessionService``
from our own Firestore messages and runs ``Runner.run_async(..., StreamingMode.SSE)``. A producer task
(``tasks.spawn``) runs the turn and pushes ``(event, payload)`` pairs into a queue; the SSE response only
drains that queue, so a client disconnect never kills settlement. The outcome is decided only by
:func:`classify_outcome`; billing settles inside one transaction together with the persisted messages.

Live findings (Vertex AI ``global``, google-adk 2.12.0, google-genai 2.31.0, 2026-10-15; reproduced by
``tests/chat/test_live.py``):

* ``gemini-3.8-flash`` accepts ``ThinkingConfig(thinking_level=LOW)`` (web plans, ``thinking_config``) and
  ``ThinkingConfig(thinking_budget=1024)`` (``portable_thinking_config``, A2A ``FallbackModel``). No
  thought parts are returned (``include_thoughts`` stays false).
* Built-in ``google_search`` mixed with a function tool (``save_record``) in one request works on
  ``gemini-3.8-flash`` in native mode: the model called the function and searched in the same turn
  (≈12 s). A function call is already present in a ``partial=True`` event (same id) before the
  ``partial=False`` one, so tool lines are taken from non-partial events only. The first grounding
  metadata can carry ``web_search_queries`` with zero ``grounding_chunks``; chunks arrive with the answer.
* Bypass mode (``GoogleSearchTool(bypass_multi_tools_limit=True)`` → ``google_search_agent``) also works,
  but the search sub-agent ran ~10 searches and the turn took ≈128 s, and the propagated
  ``grounding_metadata`` is a camelCase ``dict`` instead of ``types.GroundingMetadata`` (normalised in
  ``chat.events``). Bypass is therefore used only when a model that may serve the request is not
  gemini-3* (see ``agent_factory.search_mode_for``).
* ADK 2.12 does not raise model or tool failures out of ``run_async``: its workflow node runner yields an
  error ``Event`` (``error_code`` = API status name such as ``RESOURCE_EXHAUSTED`` or the exception class
  name, ``error_message`` = ``str(exc)`` starting with the HTTP code). Fallback and ``rate_limited`` are
  therefore decided from that event (:func:`failure_status`). An empty model reply also becomes an error
  event (``MODEL_RETURNED_NO_CONTENT``), so it surfaces as ``agent_run_failed`` + outcome ``failed``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.api_core import exceptions as gexc
from google.cloud import firestore
from google.genai import types
from pydantic import BaseModel

from topskill import tasks
from topskill.auth import CurrentUser
from topskill.chat.agent_factory import (
    THINKING_LEVEL,
    agent_name,
    build_llm_agent,
    chat_models,
    live_model,
    search_mode_for,
)
from topskill.chat.deps import Deps, get_deps, is_stub
from topskill.chat.events import SAVE_RECORD_TOOL, TurnState
from topskill.chat.history import load_history, seed_session
from topskill.chat.prompt import build_instruction
from topskill.chat.skill_tools import make_skill_tools, skills_fit_inline
from topskill.config import get_settings
from topskill.db import Col, get_db, utcnow
from topskill.domain import (
    AgentDoc,
    AgentVersion,
    ConversationDoc,
    HistoryItem,
    MessageCharge,
    MessageDoc,
    RoundCharge,
    SaveResult,
)
from topskill.errors import Codes, current_trace_id
from topskill.events import (
    CitationsData,
    DoneBilling,
    DoneData,
    DoneUsage,
    EventName,
    StatusData,
    StreamErrorData,
)
from topskill.gemini import RETRIABLE_STATUS_CODES, error_status, is_retriable, thinking_config

log = logging.getLogger(__name__)

APP_NAME = "topskill"
TITLE_MAX = 80

Outcome = Literal["ok", "failed", "aborted"]
Frame = tuple[str, BaseModel]
Emit = Callable[[Frame], None]

ERROR_MESSAGES: dict[str, str] = {
    Codes.TURN_TIMEOUT: "The reply took too long. Please try again.",
    Codes.AGENT_RUN_FAILED: "The agent could not finish this reply. Please try again.",
    Codes.RATE_LIMITED: "The model is busy right now. Please try again in a moment.",
    Codes.SERVICE_UNAVAILABLE: "Your message could not be saved. Please try again.",
    Codes.INTERNAL_ERROR: "Something went wrong. Please try again.",
}


def classify_outcome(*, abort_requested: bool, runner_finished: bool, answer_text: str) -> Outcome:
    """``aborted`` iff the stop actually interrupted the runner; else ``failed`` iff no visible text
    (with or without an exception); else ``ok`` (a counted round, possibly carrying an error)."""
    if abort_requested and not runner_finished:
        return "aborted"
    if answer_text.strip() == "":
        return "failed"
    return "ok"


@dataclass
class TurnContext:
    agent: AgentDoc
    version: AgentVersion
    user: CurrentUser
    conversation_id: str
    conversation_is_new: bool
    turn_id: str
    request_id: str
    message: str
    abort: asyncio.Event = field(default_factory=asyncio.Event)
    started_at: datetime = field(default_factory=utcnow)


@dataclass
class _Attempt:
    state: TurnState
    runner_finished: bool = False
    interrupted: bool = False  # stopped by the abort flag
    timed_out: bool = False
    exc: BaseException | None = None


@dataclass
class _Prepared:
    history: list[HistoryItem]
    instruction: str
    has_knowledge: bool
    saved_results: list[SaveResult]


def conversation_title(message: str) -> str:
    return " ".join(message.split())[:TITLE_MAX]


def _error_frame(code: str) -> Frame:
    return (
        EventName.ERROR,
        StreamErrorData(
            code=code,
            message=ERROR_MESSAGES.get(code, ERROR_MESSAGES[Codes.INTERNAL_ERROR]),
            trace_id=current_trace_id(),
        ),
    )


# ADK 2.12 does not raise model/tool failures out of ``run_async``: its workflow node runner turns them
# into an error ``Event`` (``error_code`` = the API status name such as "RESOURCE_EXHAUSTED", else the
# exception class name; ``error_message`` = ``str(exc)``, which for google-genai starts with the HTTP code).
_STATUS_NAMES: dict[str, int] = {
    "RESOURCE_EXHAUSTED": 429,
    "UNAVAILABLE": 503,
    "INTERNAL": 500,
    "DEADLINE_EXCEEDED": 504,
    "NOT_FOUND": 404,
}
_LEADING_STATUS_RE = re.compile(r"^\s*(\d{3})\b")


def failure_status(attempt: _Attempt) -> int | None:
    """HTTP-ish status of the failure that ended an attempt (raised exception or ADK error event)."""
    if attempt.exc is not None:
        return error_status(attempt.exc)
    state = attempt.state
    if not state.error_code:
        return None
    match = _LEADING_STATUS_RE.match(state.error_message or "")
    if match:
        return int(match.group(1))
    return _STATUS_NAMES.get(state.error_code)


def attempt_retriable(attempt: _Attempt) -> bool:
    """A failure the next fallback plan may retry (429/5xx/404), only if nothing was shown yet."""
    if attempt.interrupted or attempt.timed_out or attempt.state.visible:
        return False
    if attempt.exc is not None:
        return is_retriable(attempt.exc)
    return failure_status(attempt) in RETRIABLE_STATUS_CODES


def failure_code(attempt: _Attempt) -> str:
    if isinstance(attempt.exc, LlmCallsLimitExceededError):
        return Codes.AGENT_RUN_FAILED
    if failure_status(attempt) == 429:
        return Codes.RATE_LIMITED
    return Codes.AGENT_RUN_FAILED


async def _prepare(ctx: TurnContext, deps: Deps) -> _Prepared:
    version = ctx.version
    history = [] if ctx.conversation_is_new else await load_history(ctx.conversation_id)
    try:
        has_knowledge = bool(await deps.knowledge.has_ready_files(ctx.agent.id))
    except Exception:
        log.warning("knowledge_check_failed agent_id=%s", ctx.agent.id, exc_info=True)
        has_knowledge = False
    record_section: str | None = None
    if version.record_schema is not None:
        record_section = await deps.records.prompt_section(
            version.record_schema, ctx.agent.id, ctx.conversation_id
        )
    instruction = await build_instruction(
        ctx.agent, version, channel="web", has_knowledge=has_knowledge, record_section=record_section or None
    )
    return _Prepared(history=history, instruction=instruction, has_knowledge=has_knowledge, saved_results=[])


async def _attempt(
    ctx: TurnContext, deps: Deps, prep: _Prepared, model_name: str, emit: Emit, deadline: float
) -> _Attempt:
    """One fallback plan with ``model_name``. Never raises (except cancellation)."""
    settings = get_settings()
    version = ctx.version
    collector = deps.knowledge.new_collector()
    state = TurnState(collector=collector)
    attempt = _Attempt(state=state)

    tools: list = []
    if prep.has_knowledge:
        tools.append(deps.knowledge.make_search_tool(ctx.agent.id, collector))
    tools.extend(make_skill_tools(version.skills, inline=skills_fit_inline(version.skills)))
    if version.record_schema is not None:
        tools.append(
            deps.records.make_save_record_tool(
                schema=version.record_schema,
                agent=ctx.agent,
                version_n=version.n,
                conversation_id=ctx.conversation_id,
                visitor=ctx.user,
                on_saved=prep.saved_results.append,
            )
        )
    if settings.llm_mode == "fake":
        from topskill.testing import fake_llm

        model = fake_llm.make_fake_model(model_name)
    else:
        model = live_model(model_name)
    llm_agent = build_llm_agent(
        ctx.agent,
        version,
        model=model,
        instruction=prep.instruction,
        tools=tools,
        search_mode=search_mode_for(
            [model_name], web_enabled=version.web_search_enabled, has_function_tools=bool(tools)
        ),
        temperature=version.temperature,
        thinking=thinking_config(model_name, THINKING_LEVEL),
    )
    session_service = InMemorySessionService()
    await seed_session(
        session_service,
        app_name=APP_NAME,
        user_id=ctx.user.uid,
        session_id=ctx.conversation_id,
        history=prep.history,
        agent_name=agent_name(ctx.agent.slug),
    )
    runner = Runner(app_name=APP_NAME, agent=llm_agent, session_service=session_service)
    events = runner.run_async(
        user_id=ctx.user.uid,
        session_id=ctx.conversation_id,
        new_message=types.Content(role="user", parts=[types.Part(text=ctx.message)]),
        run_config=RunConfig(streaming_mode=StreamingMode.SSE, max_llm_calls=settings.max_llm_calls_per_turn),
    )
    loop = asyncio.get_running_loop()
    abort_wait = asyncio.ensure_future(ctx.abort.wait())
    try:
        while True:
            if ctx.abort.is_set():
                attempt.interrupted = True
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                attempt.timed_out = attempt.runner_finished = True
                break
            nxt = asyncio.ensure_future(events.__anext__())
            done, _ = await asyncio.wait(
                {nxt, abort_wait},
                timeout=min(float(settings.turn_idle_timeout_s), remaining),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if nxt not in done:
                nxt.cancel()
                with contextlib.suppress(BaseException):
                    await nxt
                if abort_wait in done:
                    attempt.interrupted = True
                else:
                    attempt.timed_out = attempt.runner_finished = True
                break
            try:
                ev = nxt.result()
            except StopAsyncIteration:
                attempt.runner_finished = True
                break
            except Exception as exc:
                attempt.exc = exc
                attempt.runner_finished = True
                break
            for frame in state.map_event(ev):
                emit(frame)
            if state.error_code:
                attempt.runner_finished = True
                break
    finally:
        abort_wait.cancel()
        with contextlib.suppress(BaseException):
            await abort_wait
        with contextlib.suppress(BaseException):
            await events.aclose()
    return attempt


def _message_writer(ctx: TurnContext, attempt_state: TurnState, outcome: Outcome, error_code: str | None):
    """The chat writer of SPEC §8.5 (writes only; T2 already read the conversation doc)."""
    succeeded = outcome == "ok"

    def write(tx, charge: RoundCharge, conversation_exists: bool) -> None:
        now = utcnow()
        db = get_db()
        conv_ref = db.collection(Col.CONVERSATIONS).document(ctx.conversation_id)
        messages = conv_ref.collection(Col.MESSAGES)
        msg_charge = (
            MessageCharge(
                charged_cents=charge.charged_cents,
                free=charge.charged_cents == 0,
                round_index=charge.round_index,
            )
            if charge.outcome in ("charged", "free")
            else None
        )
        user_msg = MessageDoc(
            id=f"u_{ctx.request_id}",
            role="user",
            text=ctx.message,
            status="ok" if succeeded else outcome,
            turn_id=ctx.turn_id,
            request_id=ctx.request_id,
            created_at=ctx.started_at,
        )
        collector = attempt_state.collector
        model_msg = MessageDoc(
            id=f"m_{ctx.request_id}",
            role="model",
            text=attempt_state.answer_text,
            status=outcome,
            turn_id=ctx.turn_id,
            request_id=ctx.request_id,
            citations=collector.sources(),
            search_entry_point_html=collector.search_entry_point_html,
            tools=attempt_state.tool_lines(),
            usage=attempt_state.usage,
            charge=msg_charge,
            error_code=error_code,
            created_at=now,
        )
        tx.set(messages.document(user_msg.id), user_msg.model_dump(mode="python"))
        tx.set(messages.document(model_msg.id), model_msg.model_dump(mode="python"))
        if not conversation_exists:
            conv = ConversationDoc(
                id=ctx.conversation_id,
                agent_id=ctx.agent.id,
                agent_slug=ctx.agent.slug,
                owner_uid=ctx.agent.owner_uid,
                visitor_uid=ctx.user.uid,
                title=conversation_title(ctx.message),
                rounds_ok=1 if succeeded else 0,
                message_count=2,
                last_turn_id=ctx.turn_id,
                created_at=now,
                updated_at=now,
            )
            tx.create(conv_ref, conv.model_dump(mode="python"))
        else:
            tx.update(
                conv_ref,
                {
                    "rounds_ok": firestore.Increment(1 if succeeded else 0),
                    "message_count": firestore.Increment(2),
                    "last_turn_id": ctx.turn_id,
                    "updated_at": now,
                },
            )

    return write


def _done_billing(charge: RoundCharge) -> DoneBilling:
    return DoneBilling(
        charged_cents=charge.charged_cents,
        free=charge.outcome == "free" or (charge.outcome == "duplicate" and charge.charged_cents == 0),
        round_index=charge.round_index,
        free_rounds_left=charge.free_rounds_left,
        balance_cents=charge.balance_cents,
    )


async def run_turn(ctx: TurnContext, emit: Emit) -> None:
    """The producer: ``status`` → deltas/tools → ``citations``? → ``error``? → ``done`` (exactly once)."""
    settings = get_settings()
    deps = get_deps()
    log_extra = f"conversation_id={ctx.conversation_id} request_id={ctx.request_id} agent_id={ctx.agent.id}"
    emit(
        (
            EventName.STATUS,
            StatusData(
                conversation_id=ctx.conversation_id,
                turn_id=ctx.turn_id,
                request_id=ctx.request_id,
                model=ctx.version.model,
            ),
        )
    )
    done_sent = False
    error_sent = False
    state = TurnState(collector=deps.knowledge.new_collector())
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.turn_total_timeout_s
        turn_error: str | None = None
        attempt = _Attempt(state=state)
        prep: _Prepared | None = None
        try:
            prep = await _prepare(ctx, deps)
        except Exception as exc:
            log.error("turn_prepare_failed %s error=%r", log_extra, exc, exc_info=True)
            turn_error = (
                Codes.SERVICE_UNAVAILABLE if isinstance(exc, gexc.GoogleAPIError) else Codes.INTERNAL_ERROR
            )
            attempt.runner_finished = True
        if prep is not None:
            models = chat_models(ctx.version)
            for i, model_name in enumerate(models):
                attempt = await _attempt(ctx, deps, prep, model_name, emit, deadline)
                if i + 1 < len(models) and attempt_retriable(attempt):
                    log.warning(
                        "turn_model_fallback %s model=%s status=%s",
                        log_extra,
                        model_name,
                        failure_status(attempt),
                    )
                    continue
                break
            state = attempt.state
            if attempt.timed_out:
                turn_error = Codes.TURN_TIMEOUT
                log.warning("turn_timeout %s", log_extra)
            elif attempt.exc is not None:
                turn_error = failure_code(attempt)
                log.error("turn_run_failed %s error=%r", log_extra, attempt.exc, exc_info=attempt.exc)
            elif state.error_code:
                turn_error = failure_code(attempt)
                log.error(
                    "turn_error_event %s error_code=%s error_message=%s",
                    log_extra,
                    state.error_code,
                    (state.error_message or "")[:300],
                )

        outcome = classify_outcome(
            abort_requested=ctx.abort.is_set(),
            runner_finished=attempt.runner_finished,
            answer_text=state.answer_text,
        )
        for frame in state.close_unfinished(interrupted=outcome == "aborted"):
            emit(frame)
        collector = state.collector
        sources = collector.sources()
        if sources or collector.search_entry_point_html or collector.web_search_queries:
            emit(
                (
                    EventName.CITATIONS,
                    CitationsData(
                        sources=sources,
                        search_entry_point_html=collector.search_entry_point_html,
                        web_search_queries=collector.web_search_queries,
                    ),
                )
            )

        charge: RoundCharge | None = None
        try:
            charge = await deps.billing.settle_round(
                agent=ctx.agent,
                payer=ctx.user,
                conversation_id=ctx.conversation_id,
                request_id=ctx.request_id,
                succeeded=outcome == "ok",
                model=state.model or ctx.version.model,
                write_messages=_message_writer(ctx, state, outcome, turn_error),
            )
        except Exception as exc:
            log.error("settlement_failed %s error=%r", log_extra, exc, exc_info=True)

        error_code = Codes.SERVICE_UNAVAILABLE if charge is None else turn_error
        if error_code:
            emit(_error_frame(error_code))
            error_sent = True

        if (
            charge is not None
            and outcome == "ok"
            and ctx.version.record_schema is not None
            and SAVE_RECORD_TOOL not in state.called_tools
        ):
            _schedule_extraction(ctx, deps)

        emit(
            (
                EventName.DONE,
                DoneData(
                    conversation_id=ctx.conversation_id
                    if charge is not None or not ctx.conversation_is_new
                    else None,
                    message_id=f"m_{ctx.request_id}" if charge is not None else None,
                    turn_id=ctx.turn_id,
                    outcome=outcome,
                    usage=DoneUsage(
                        request_id=ctx.request_id,
                        input_tokens=state.usage.input_tokens if state.usage else None,
                        output_tokens=state.usage.output_tokens if state.usage else None,
                        model=(state.usage.model if state.usage else None) or state.model,
                    ),
                    billing=_done_billing(charge) if charge is not None else None,
                ),
            )
        )
        done_sent = True
        log.info(
            "turn_done %s outcome=%s error=%s charged=%s",
            log_extra,
            outcome,
            error_code,
            charge.charged_cents if charge else None,
        )
    except Exception as exc:  # a bug in our own code: still end the stream properly
        log.error("turn_internal_error %s error=%r", log_extra, exc, exc_info=True)
        if not done_sent:
            for frame in state.close_unfinished(interrupted=False):
                emit(frame)
            if not error_sent:
                emit(_error_frame(Codes.INTERNAL_ERROR))
            emit(
                (
                    EventName.DONE,
                    DoneData(
                        conversation_id=None if ctx.conversation_is_new else ctx.conversation_id,
                        message_id=None,
                        turn_id=ctx.turn_id,
                        outcome="failed",
                        usage=None,
                        billing=None,
                    ),
                )
            )


def _schedule_extraction(ctx: TurnContext, deps: Deps) -> None:
    """[SHOULD] record extraction fallback when the model did not call save_record (skipped for stubs)."""
    extract = getattr(deps.records, "extract", None)
    if extract is None or is_stub(deps.records):
        return
    tasks.spawn(
        extract(agent=ctx.agent, version=ctx.version, conversation_id=ctx.conversation_id, visitor=ctx.user),
        name=f"extract:{ctx.conversation_id}",
    )


def start_turn(ctx: TurnContext, *, on_finish: Callable[[], None]) -> AsyncIterator[Frame]:
    """Spawn the producer and return the consumer the SSE response drains. Closing the consumer before
    ``done`` (client disconnect) sets the abort flag; the producer still settles and calls ``on_finish``."""
    queue: asyncio.Queue[Frame | None] = asyncio.Queue()

    async def producer() -> None:
        try:
            await run_turn(ctx, queue.put_nowait)
        finally:
            try:
                on_finish()
            finally:
                queue.put_nowait(None)

    tasks.spawn(producer(), name=f"turn:{ctx.request_id}")

    async def consumer() -> AsyncIterator[Frame]:
        finished = False
        try:
            while True:
                item = await queue.get()
                if item is None:
                    finished = True
                    return
                yield item
        finally:
            if not finished:
                ctx.abort.set()

    return consumer()
