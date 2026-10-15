"""One live voice session: browser WebSocket <-> Gemini Live (SPEC §18.1).

Three tasks run while the Live session is open:

* the client pump forwards the visitor's PCM frames to Live and handles ``stop``/``ping``;
* the model pump forwards Live audio to the browser, streams captions, runs function tools and cuts the
  conversation into :class:`~topskill.voice.rounds.Round`s (a round ends with the ``turn_complete`` of a
  turn that produced an answer, or with a barge-in before that);
* the watchdog enforces the maximum session length and the idle timeout (both end gracefully: the
  answer being spoken is finished first, at most :data:`END_GRACE_S`).

Finished rounds are settled one at a time by a settler task with ``billing.settle_round`` (T2: messages +
charge in one transaction, exactly the text chat rules). After every counted round the next round is
pre-flighted again (``billing.preflight_round``); when the visitor can no longer pay, or the conversation
is full, the session ends with the same error codes as the text chat.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
from contextlib import AsyncExitStack
from dataclasses import dataclass

from google.genai import types
from pydantic import BaseModel
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from topskill import repo, tasks
from topskill.auth import CurrentUser
from topskill.chat.deps import Deps, get_deps, is_stub, real_deps
from topskill.chat.events import describe_tool, normalize_grounding
from topskill.chat.history import load_history
from topskill.chat.prompt import build_instruction
from topskill.chat.skill_tools import make_skill_tools, skills_fit_inline
from topskill.config import get_settings
from topskill.db import new_id
from topskill.domain import AgentDoc, AgentVersion, HistoryItem, RoundCharge, RoundQuote, ToolLineDoc
from topskill.errors import ApiError, Codes, current_trace_id
from topskill.events import DoneBilling
from topskill.voice import live as live_mod
from topskill.voice.protocol import (
    MAX_AUDIO_FRAME_BYTES,
    ClosedEvent,
    ClosedReason,
    ErrorEvent,
    InterruptedEvent,
    ReadyEvent,
    RoundEvent,
    ToolEvent,
    TranscriptEvent,
    close_code_for,
)
from topskill.voice.rounds import Round, round_writer

log = logging.getLogger(__name__)

END_GRACE_S = 20.0
SETTLE_DRAIN_S = 30.0
HISTORY_MAX_CHARS = 8_000

VOICE_SECTION = """## Voice call
You are talking with the visitor in a live voice call; everything you say is spoken aloud.
- Answer in short spoken sentences, usually two to four. No Markdown, lists, tables, emoji or URLs.
- Never read citation markers such as [1] aloud; name the document instead ("the visa guide says …").
- If the visitor interrupts you, stop and listen.
- Speak the visitor's language."""


# --------------------------------------------------------------------------- setup


@dataclass
class VoiceSetup:
    agent: AgentDoc
    version: AgentVersion
    user: CurrentUser
    conversation_id: str
    conversation_is_new: bool
    rounds_before: int
    quote: RoundQuote


async def prepare(slug: str, user: CurrentUser, conversation_id: str | None) -> VoiceSetup:
    """The checks of the text chat before a stream opens: agent online, conversation scoped to this
    visitor and not full, and ``preflight_round`` (410 / 402) for the first round."""
    settings = get_settings()
    agent = await repo.get_public_agent(slug)
    version = await repo.get_active_version(agent)
    if conversation_id is not None:
        conv = await repo.get_scoped_conversation(conversation_id, agent_id=agent.id, visitor_uid=user.uid)
        if conv.rounds_ok >= settings.max_rounds_per_conversation:
            raise ApiError(
                400,
                Codes.CONVERSATION_LIMIT_REACHED,
                "This conversation is full. Please start a new one.",
                {"max_rounds": settings.max_rounds_per_conversation},
            )
        cid, is_new, rounds_before = conv.id, False, conv.rounds_ok
    else:
        cid, is_new, rounds_before = new_id("cnv"), True, 0
    quote = await get_deps().billing.preflight_round(agent, user)
    return VoiceSetup(
        agent=agent,
        version=version,
        user=user,
        conversation_id=cid,
        conversation_is_new=is_new,
        rounds_before=rounds_before,
        quote=quote,
    )


def history_section(history: list[HistoryItem], max_chars: int = HISTORY_MAX_CHARS) -> str:
    """The newest messages of the conversation (text and earlier voice rounds) as a prompt section."""
    lines: list[str] = []
    used = 0
    for item in reversed(history):
        line = f"{'Visitor' if item.role == 'user' else 'You'}: {item.text}"
        if used + len(line) > max_chars:
            break
        used += len(line)
        lines.append(line)
    if not lines:
        return ""
    lines.reverse()
    return "## Conversation so far\n" + "\n".join(lines)


async def build_voice_instruction(
    agent: AgentDoc, version: AgentVersion, *, has_knowledge: bool, history: list[HistoryItem]
) -> str:
    """The text chat's instruction (persona, playbooks, knowledge and web rules) plus voice rules and
    the conversation so far. No record section: ``save_record`` is not offered in voice; records are
    filled by the extraction fallback after each counted round."""
    base = await build_instruction(
        agent, version, channel="web", has_knowledge=has_knowledge, record_section=None
    )
    sections = [base, VOICE_SECTION]
    past = history_section(history)
    if past:
        sections.append(past)
    return "\n\n".join(sections)


def done_billing(charge: RoundCharge) -> DoneBilling:
    return DoneBilling(
        charged_cents=charge.charged_cents,
        free=charge.outcome == "free" or (charge.outcome == "duplicate" and charge.charged_cents == 0),
        round_index=charge.round_index,
        free_rounds_left=charge.free_rounds_left,
        balance_cents=charge.balance_cents,
    )


# --------------------------------------------------------------------------- session


class VoiceSession:
    TICK_S = 0.5  # watchdog resolution (tests lower it)

    def __init__(self, ws: WebSocket, setup: VoiceSetup, *, deps: Deps | None = None) -> None:
        self.ws = ws
        self.setup = setup
        self.deps = deps or get_deps()
        self.id = "vs_" + secrets.token_hex(10)
        self.finished = asyncio.Event()  # set by the router after every lock is released
        self.reason: ClosedReason | None = None
        self.ending: ClosedReason | None = None  # graceful end requested
        self.ending_deadline = 0.0
        self.error_status: int | None = None
        self.model_name = ""
        self.rounds_ok = 0
        self.rounds_saved = 0
        self.persisted = not setup.conversation_is_new
        self._stop = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._settle_queue: asyncio.Queue[Round | None] = asyncio.Queue()
        self._bridge: live_mod.ToolBridge | None = None
        self._loop = asyncio.get_running_loop()
        self.started_at = self._loop.time()
        self.last_activity = self.started_at
        self.round = self._new_round()
        self._log = (
            f"voice_session={self.id} conversation_id={setup.conversation_id} agent_id={setup.agent.id} "
            f"uid={setup.user.uid}"
        )

    # ---- lifecycle helpers
    def _new_round(self) -> Round:
        return Round(collector=self.deps.knowledge.new_collector(), model=self.model_name)

    def replace(self) -> None:
        """Another session of the same account starts: end this one now."""
        self.stop("replaced")

    def stop(self, reason: ClosedReason) -> None:
        if self.reason is None:
            self.reason = reason
        self._stop.set()

    def _in_progress(self) -> bool:
        rnd = self.round
        return bool(rnd.user_parts or rnd.tools or rnd.answered)

    def end_gracefully(self, reason: ClosedReason) -> None:
        """Stop listening; let the answer being produced finish (≤ END_GRACE_S), then end."""
        if self.ending or self._stop.is_set():
            return
        if not self._in_progress():
            self.stop(reason)
            return
        self.ending = reason
        self.ending_deadline = self._loop.time() + END_GRACE_S

    def _activity(self) -> None:
        self.last_activity = self._loop.time()

    # ---- sending
    @property
    def ws_open(self) -> bool:
        return (
            self.ws.client_state == WebSocketState.CONNECTED
            and self.ws.application_state == WebSocketState.CONNECTED
        )

    async def send_event(self, event: BaseModel) -> None:
        async with self._send_lock:
            if not self.ws_open:
                return
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await self.ws.send_text(event.model_dump_json())

    async def send_audio(self, data: bytes) -> None:
        async with self._send_lock:
            if not self.ws_open:
                return
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await self.ws.send_bytes(data)

    async def fail(self, error: ApiError) -> None:
        """Terminal error: send the envelope and end the session (close code 4000 + status)."""
        if self.error_status is None:
            self.error_status = error.status
            await self.send_event(
                ErrorEvent(
                    code=error.code, message=error.message, trace_id=current_trace_id(), detail=error.detail
                )
            )
        self.stop("error")

    # ---- run
    async def run(self) -> None:
        settings = get_settings()
        prep_instruction, bridge = await self._prepare_model()
        self._bridge = bridge
        config = live_mod.build_live_config(
            instruction=prep_instruction,
            declarations=bridge.declarations,
            web_search=self.setup.version.web_search_enabled,
            temperature=self.setup.version.temperature,
        )
        settler = tasks.spawn(self._settler(), name=f"voice-settle:{self.id}")
        try:
            async with AsyncExitStack() as stack:
                session = await self._open_live(stack, config)
                self.round.model = self.model_name
                quote = self.setup.quote
                await self.send_event(
                    ReadyEvent(
                        session_id=self.id,
                        conversation_id=self.setup.conversation_id,
                        model=self.model_name,
                        max_session_s=settings.voice_max_session_s,
                        idle_timeout_s=settings.voice_idle_timeout_s,
                        next_round_charge_cents=quote.charge_cents,
                        balance_cents=quote.balance_cents,
                    )
                )
                log.info("voice_session_started %s model=%s", self._log, self.model_name)
                await self._pump(session)
        except ApiError as exc:
            await self.fail(exc)
        finally:
            self._close_round()
            self._settle_queue.put_nowait(None)
            try:
                await asyncio.wait_for(asyncio.shield(settler), timeout=SETTLE_DRAIN_S)
            except TimeoutError:
                log.error("voice_settle_drain_timeout %s", self._log)
            await self._send_closed()
            log.info(
                "voice_session_ended %s reason=%s rounds_ok=%d rounds_saved=%d duration_s=%d",
                self._log,
                self.reason,
                self.rounds_ok,
                self.rounds_saved,
                int(self._loop.time() - self.started_at),
            )

    async def _prepare_model(self) -> tuple[str, live_mod.ToolBridge]:
        agent, version = self.setup.agent, self.setup.version
        try:
            has_knowledge = bool(await self.deps.knowledge.has_ready_files(agent.id))
        except Exception:
            log.warning("knowledge_check_failed %s", self._log, exc_info=True)
            has_knowledge = False
        history: list[HistoryItem] = []
        if not self.setup.conversation_is_new:
            try:
                history = await load_history(self.setup.conversation_id)
            except Exception:
                log.warning("voice_history_failed %s", self._log, exc_info=True)
        instruction = await build_voice_instruction(
            agent, version, has_knowledge=has_knowledge, history=history
        )
        knowledge = self.deps.knowledge

        def factory(collector) -> list:
            tools: list = []
            if has_knowledge:
                tools.append(knowledge.make_search_tool(agent.id, collector))
            tools.extend(make_skill_tools(version.skills, inline=skills_fit_inline(version.skills)))
            return tools

        return instruction, live_mod.ToolBridge(factory, knowledge.new_collector())

    async def _open_live(
        self, stack: AsyncExitStack, config: types.LiveConnectConfig
    ) -> live_mod.LiveSession:
        connector = live_mod.get_connector()
        last: BaseException | None = None
        for model, location in live_mod.live_plans():
            try:
                session = await stack.enter_async_context(
                    connector.connect(model=model, location=location, config=config)
                )
            except Exception as exc:
                last = exc
                log.warning(
                    "voice_connect_failed %s model=%s location=%s error=%r", self._log, model, location, exc
                )
                continue
            self.model_name = model
            return session
        log.error("voice_unavailable %s error=%r", self._log, last)
        raise ApiError(
            503,
            Codes.SERVICE_UNAVAILABLE,
            "Voice is temporarily unavailable. You can keep chatting by text.",
        )

    async def _pump(self, session: live_mod.LiveSession) -> None:
        pumps = [
            asyncio.create_task(self._client_pump(session), name=f"voice-client:{self.id}"),
            asyncio.create_task(self._model_pump(session), name=f"voice-model:{self.id}"),
            asyncio.create_task(self._watchdog(), name=f"voice-watchdog:{self.id}"),
        ]
        stop_wait = asyncio.create_task(self._stop.wait())
        try:
            await asyncio.wait({*pumps, stop_wait}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (*pumps, stop_wait):
                task.cancel()
            results = await asyncio.gather(*pumps, stop_wait, return_exceptions=True)
        for task, result in zip(pumps, results, strict=False):
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                log.error(
                    "voice_task_failed %s task=%s error=%r",
                    self._log,
                    task.get_name(),
                    result,
                    exc_info=result,
                )
                if self.round.answered or self.round.user_parts:
                    self.round.error_code = Codes.AGENT_RUN_FAILED
                await self.fail(
                    ApiError(
                        503, Codes.SERVICE_UNAVAILABLE, "The voice connection was lost. Please try again."
                    )
                )
        if self.reason is None:
            self.stop("server")

    # ---- client → model
    async def _client_pump(self, session: live_mod.LiveSession) -> None:
        while True:
            try:
                message = await self.ws.receive()
            except (WebSocketDisconnect, RuntimeError):
                self.stop("client")
                return
            if message.get("type") == "websocket.disconnect":
                self.stop("client")
                return
            data = message.get("bytes")
            if data is not None:
                if len(data) > MAX_AUDIO_FRAME_BYTES or len(data) % 2:
                    await self.fail(
                        ApiError(
                            400, Codes.VALIDATION_ERROR, "Audio frames must be 16-bit PCM of at most 64 KB."
                        )
                    )
                    return
                if self.ending or not data:
                    continue
                await session.send_audio(data)
                continue
            text = message.get("text")
            if text is None:
                continue
            try:
                payload = json.loads(text)
            except ValueError:
                continue
            kind = payload.get("type") if isinstance(payload, dict) else None
            if kind == "stop":
                self.stop("client")
                return
            if kind == "ping":
                await self.send_event(_Pong())

    # ---- model → client
    async def _model_pump(self, session: live_mod.LiveSession) -> None:
        async for msg in session.messages():
            await self._on_message(session, msg)
            if self._stop.is_set():
                return
        self.end_gracefully("server")
        if not self._stop.is_set():
            self.stop("server")

    async def _on_message(self, session: live_mod.LiveSession, msg: types.LiveServerMessage) -> None:
        rnd = self.round
        usage = msg.usage_metadata
        if usage is not None:
            rnd.add_usage(usage.prompt_token_count, usage.response_token_count)
        if msg.tool_call is not None and msg.tool_call.function_calls:
            await self._on_tool_call(session, msg.tool_call.function_calls)
        if msg.tool_call_cancellation is not None:
            for call_id in msg.tool_call_cancellation.ids or []:
                line = self.round.tools.get(call_id)
                if line is not None and line.status == "running":
                    line = line.model_copy(update={"status": "interrupted"})
                    self.round.tools[call_id] = line
                    await self.send_event(ToolEvent(turn_id=self.round.turn_id, tool=line))
        sc = msg.server_content
        if sc is not None:
            await self._on_content(sc)
        if msg.go_away is not None:
            log.info("voice_go_away %s time_left=%s", self._log, msg.go_away.time_left)
            self.end_gracefully("server")

    async def _on_content(self, sc: types.LiveServerContent) -> None:
        rnd = self.round
        if sc.input_transcription is not None and sc.input_transcription.text:
            self._activity()
            rnd.user_parts.append(sc.input_transcription.text)
            await self.send_event(
                TranscriptEvent(turn_id=rnd.turn_id, role="user", text=sc.input_transcription.text)
            )
        if sc.grounding_metadata is not None:
            for i, query in enumerate(
                rnd.collector.add_grounding(normalize_grounding(sc.grounding_metadata))
            ):
                line = ToolLineDoc(
                    id=f"web-{len(rnd.tools)}-{i}",
                    tool="google_search",
                    kind="web",
                    title="Searched the web",
                    target=query[:80],
                    status="completed",
                )
                rnd.tools[line.id] = line
                await self.send_event(ToolEvent(turn_id=rnd.turn_id, tool=line))
        if sc.model_turn is not None:
            for part in sc.model_turn.parts or []:
                blob = part.inline_data
                if blob is not None and blob.data and (blob.mime_type or "audio/").startswith("audio/"):
                    self._activity()
                    rnd.audio_bytes += len(blob.data)
                    await self.send_audio(blob.data)
        if sc.output_transcription is not None and sc.output_transcription.text:
            self._activity()
            rnd.model_parts.append(sc.output_transcription.text)
            await self.send_event(
                TranscriptEvent(turn_id=rnd.turn_id, role="model", text=sc.output_transcription.text)
            )
        if sc.interrupted:
            await self._on_interrupted()
        if sc.turn_complete:
            self._on_turn_complete()

    async def _on_interrupted(self) -> None:
        """Barge-in. Mid-answer: the round ends ``aborted`` (not charged). After the model already finished
        generating (audio is still playing in the browser) the round stays as settled (SPEC §7.4); the
        browser drops its queued audio either way."""
        rnd = self.round
        await self.send_event(InterruptedEvent(turn_id=rnd.turn_id))
        if rnd.answered:
            rnd.interrupted = True
            self._finalize()

    def _on_turn_complete(self) -> None:
        # A turn that only called a tool (or said nothing) is not the end of the round.
        if self.round.answered:
            self._finalize()
        if self.ending and not self._in_progress():
            self.stop(self.ending)

    async def _on_tool_call(self, session: live_mod.LiveSession, calls: list[types.FunctionCall]) -> None:
        rnd = self.round
        self._activity()
        assert self._bridge is not None
        if rnd.tool_impls is None:
            rnd.tool_impls = self._bridge.tools_for(rnd.collector)
        lines: list[tuple[types.FunctionCall, ToolLineDoc]] = []
        for i, call in enumerate(calls):
            name = call.name or ""
            kind, title, target = describe_tool(name, call.args)
            line = ToolLineDoc(
                id=call.id or f"call-{len(rnd.tools)}-{i}",
                tool=name,
                kind=kind,
                title=title,
                target=target,
                status="running",
            )
            rnd.tools[line.id] = line
            lines.append((call, line))
            await self.send_event(ToolEvent(turn_id=rnd.turn_id, tool=line))
        results = await asyncio.gather(
            *(live_mod.ToolBridge.call(rnd.tool_impls, call.name or "", call.args) for call, _ in lines)
        )
        responses: list[types.FunctionResponse] = []
        for (call, line), result in zip(lines, results, strict=True):
            status = "error" if "error" in result and "results" not in result else "completed"
            if rnd.tools.get(line.id, line).status == "running":
                done = line.model_copy(update={"status": status})
                rnd.tools[line.id] = done
                await self.send_event(ToolEvent(turn_id=rnd.turn_id, tool=done))
            responses.append(types.FunctionResponse(id=call.id, name=call.name, response=result))
        await session.send_tool_responses(responses)

    # ---- limits
    async def _watchdog(self) -> None:
        settings = get_settings()
        while not self._stop.is_set():
            await asyncio.sleep(self.TICK_S)
            now = self._loop.time()
            if self.ending:
                if now >= self.ending_deadline:
                    self.stop(self.ending)
                continue
            if now - self.started_at >= settings.voice_max_session_s:
                self.end_gracefully("max_duration")
            elif now - self.last_activity >= settings.voice_idle_timeout_s and not self._in_progress():
                self.end_gracefully("idle")

    # ---- rounds and settlement
    def _finalize(self) -> None:
        rnd = self.round
        self.round = self._new_round()
        if rnd.worth_saving:
            self._settle_queue.put_nowait(rnd)

    def _close_round(self) -> None:
        """At the end of the session: a started but unfinished answer is ``aborted`` (never charged)."""
        rnd = self.round
        if rnd.answered or rnd.tools:
            rnd.interrupted = rnd.error_code is None
        self._finalize()

    async def _settler(self) -> None:
        while True:
            rnd = await self._settle_queue.get()
            if rnd is None:
                return
            try:
                await self._settle(rnd)
            except Exception:
                log.exception("voice_settle_internal_error %s request_id=%s", self._log, rnd.request_id)

    async def _settle(self, rnd: Round) -> None:
        setup = self.setup
        outcome = rnd.outcome()
        try:
            charge = await self.deps.billing.settle_round(
                agent=setup.agent,
                payer=setup.user,
                conversation_id=setup.conversation_id,
                request_id=rnd.request_id,
                succeeded=outcome == "ok",
                model=rnd.model or self.model_name,
                write_messages=round_writer(
                    rnd,
                    agent=setup.agent,
                    visitor=setup.user,
                    conversation_id=setup.conversation_id,
                    outcome=outcome,
                ),
            )
        except Exception as exc:
            log.error(
                "voice_settlement_failed %s request_id=%s error=%r",
                self._log,
                rnd.request_id,
                exc,
                exc_info=True,
            )
            await self.fail(
                ApiError(
                    503, Codes.SERVICE_UNAVAILABLE, "Your voice message could not be saved. Please try again."
                )
            )
            return
        self.persisted = True
        self.rounds_saved += 1
        if outcome == "ok":
            self.rounds_ok += 1
        log.info(
            "voice_round_settled %s request_id=%s outcome=%s charged=%d",
            self._log,
            rnd.request_id,
            outcome,
            charge.charged_cents,
        )
        await self.send_event(
            RoundEvent(
                turn_id=rnd.turn_id,
                conversation_id=setup.conversation_id,
                message_id=f"m_{rnd.request_id}",
                outcome=outcome,
                citations=rnd.collector.sources(),
                billing=done_billing(charge),
            )
        )
        if outcome != "ok":
            return
        self._schedule_extraction()
        if self._stop.is_set() or self.ending:
            return
        settings = get_settings()
        if setup.rounds_before + self.rounds_ok >= settings.max_rounds_per_conversation:
            await self.fail(
                ApiError(
                    400,
                    Codes.CONVERSATION_LIMIT_REACHED,
                    "This conversation is full. Please start a new one.",
                    {"max_rounds": settings.max_rounds_per_conversation},
                )
            )
            return
        try:
            await self.deps.billing.preflight_round(setup.agent, setup.user)
        except ApiError as exc:
            await self.fail(exc)

    def _schedule_extraction(self) -> None:
        """Records are filled by the extraction fallback (voice offers no ``save_record`` tool)."""
        setup = self.setup
        records = self.deps.records
        extract = getattr(records, "extract", None)
        if setup.version.record_schema is None or extract is None or is_stub(records):
            return
        if get_settings().llm_mode == "fake" and records is real_deps().records:
            return
        tasks.spawn(
            extract(
                agent=setup.agent,
                version=setup.version,
                conversation_id=setup.conversation_id,
                visitor=setup.user,
            ),
            name=f"extract:{setup.conversation_id}",
        )

    async def _send_closed(self) -> None:
        await self.send_event(
            ClosedEvent(
                reason=self.reason or "server",
                conversation_id=self.setup.conversation_id if self.persisted else None,
                rounds=self.rounds_ok,
                duration_s=int(self._loop.time() - self.started_at),
            )
        )
        if self.ws_open:
            code = close_code_for(self.error_status) if self.error_status is not None else 1000
            with contextlib.suppress(Exception):
                await self.ws.close(code=code)


class _Pong(BaseModel):
    type: str = "pong"
