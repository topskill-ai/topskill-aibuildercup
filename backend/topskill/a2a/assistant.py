"""Enterprise Assistant demo caller (SPEC §9.4, §7.5).

An ADK agent that discovers marketplace agents (directory ranking + Agent Cards fetched over HTTP) and
hires them over A2A, paying with the mandate the signed-in user authorised. Every call is pre-checked
in process (agent, mandate audience, per-call cap, budget, balances) so a call that would be rejected is
never sent; accepted calls go over real HTTP to ``/a2a/{slug}`` with the mandate in ``X-AP2-Mandate``.
Tools push ``a2a_discover`` / ``a2a_call`` / ``a2a_result`` frames into a per-request queue that the SSE
response drains.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import uuid
from collections.abc import AsyncIterator
from typing import Literal

import httpx
from a2a.client import A2AClientError, ClientCallContext, ClientConfig, create_client
from a2a.types import Message, Part, Role, SendMessageRequest
from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.events import Event
from google.adk.models import BaseLlm, FallbackModel, Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel, Field, field_validator
from starlette.responses import StreamingResponse

from topskill import ratelimit, tasks
from topskill.auth import CurrentUser
from topskill.config import get_settings
from topskill.db import Col, get_db
from topskill.domain import AgentDoc, VerifiedMandate
from topskill.errors import ApiError, Codes, current_trace_id
from topskill.events import (
    A2ACallData,
    A2ADiscoverData,
    A2AResultData,
    AssistantDoneData,
    DiscoveredAgent,
    DoneUsage,
    EventName,
    MandateCounters,
    StatusData,
    StreamErrorData,
    TextDeltaData,
    ToolEndData,
    ToolStartData,
)
from topskill.gemini import is_retriable, model_chain, portable_thinking_config
from topskill.sse import sse_response

from . import _deps, directory
from .cards import card_price_cents, endpoint_url
from .executor import load_a2a_agent
from .mandates import call_id_for, mandate_error, verify_token

log = logging.getLogger(__name__)

APP_NAME = "topskill-assistant"
AGENT_NAME = "enterprise_assistant"
MAX_CALLS_PER_TURN = 3
DISCOVER_TOP_N = 3
ANSWER_TO_MODEL_CHARS = 6000
ANSWER_PREVIEW_CHARS = 300
REQUEST_PREVIEW_CHARS = 120
RATE_LIMIT_PER_HOUR = 60

ASSISTANT_PROMPT = """You are the Enterprise Assistant for Acme Corp employees. You can hire specialist \
agents from the TopSkill marketplace over A2A.

How to work:
- Always call find_agents first with a short description of what the employee needs.
- Choose at most three relevant agents from the results. Send each one a self-contained request with \
call_agent: include every fact the specialist needs, because it cannot ask follow-up questions.
- Then write a concise final answer that attributes points to the agent that provided them \
(for example "Per Visa Advisor: ...") and states the total credits spent (100 cents = 1.00 credit).
- Never invent agent answers. If a call fails or is rejected, say so briefly (it was not charged) and \
continue without it.
- Reply in the employee's language. Use short Markdown sections or lists when helpful."""


class AssistantMessage(BaseModel):
    role: Literal["user", "model"]
    text: str = Field(max_length=4000)


class AssistantRequest(BaseModel):
    messages: list[AssistantMessage] = Field(min_length=1, max_length=12)
    mandate_token: str = Field(min_length=1, max_length=8192)

    @field_validator("messages")
    @classmethod
    def _last_is_user(cls, value: list[AssistantMessage]) -> list[AssistantMessage]:
        if value[-1].role != "user" or not value[-1].text.strip():
            raise ValueError("the last message must be a non-empty user message")
        return value


# --------------------------------------------------------------------------- seams (tests substitute)


def build_model() -> BaseLlm:
    """``FallbackModel`` over the ``assistant`` chain (one config reaches several models, so thinking is
    configured with ``portable_thinking_config``)."""
    if get_settings().llm_mode != "live":
        raise ApiError(503, Codes.SERVICE_UNAVAILABLE, "The assistant model is not available in this mode.")
    chain = model_chain("assistant")
    primary = Gemini(model=chain[0], retry_options=types.HttpRetryOptions(initial_delay=1, attempts=2))
    if len(chain) == 1:
        return primary
    return FallbackModel(models=[primary, *chain[1:]])


def http_client(timeout: float) -> httpx.AsyncClient:
    """HTTP client for Agent Cards and A2A calls (``trust_env=False``: loopback must bypass proxies)."""
    return httpx.AsyncClient(timeout=timeout, trust_env=False)


# --------------------------------------------------------------------------- helpers


_HTTP_STATUS_RE = re.compile(r"HTTP Error (\d{3})")


def a2a_error_code(err: BaseException) -> tuple[str, int | None]:
    """``(error_code, http_status)`` of a failed A2A client call.

    a2a-sdk 1.2.2's JSON-RPC transport turns a non-2xx into ``A2AClientError("HTTP Error 402: …")`` and
    drops the body; the ``httpx.HTTPStatusError`` cause still holds the (already read) response."""
    cause = err.__cause__
    if isinstance(cause, httpx.HTTPStatusError):
        status = cause.response.status_code
        try:
            code = cause.response.json()["error"]["data"]["code"]
            if isinstance(code, str) and code:
                return code, status
        except Exception:
            pass
        return _code_for_status(status), status
    match = _HTTP_STATUS_RE.search(str(err))
    if match:
        status = int(match.group(1))
        return _code_for_status(status), status
    return Codes.UPSTREAM_ERROR, None


def _code_for_status(status: int) -> str:
    return {404: Codes.AGENT_NOT_FOUND, 410: Codes.AGENT_OFFLINE, 429: Codes.RATE_LIMITED}.get(
        status, Codes.UPSTREAM_ERROR
    )


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _texts(parts: list[dict] | None) -> str:
    return "".join(p.get("text", "") for p in parts or [] if isinstance(p, dict))


def extract_result(responses: list[dict]) -> tuple[str, str]:
    """``(state, answer)`` from the ``StreamResponse`` dicts of one ``send_message``.

    ``state`` is the final task state (``TASK_STATE_COMPLETED`` …) or ``"message"`` for a direct message.
    """
    state, answer, artifacts = "", "", ""
    for item in responses:
        if "task" in item:
            task = item["task"]
            state = (task.get("status") or {}).get("state", state)
            artifacts = "".join(_texts(a.get("parts")) for a in task.get("artifacts") or [])
            if not artifacts:
                answer = _texts(((task.get("status") or {}).get("message") or {}).get("parts")) or answer
        elif "message" in item:
            state, answer = "message", _texts(item["message"].get("parts"))
        elif "artifactUpdate" in item:
            artifacts += _texts((item["artifactUpdate"].get("artifact") or {}).get("parts"))
        elif "statusUpdate" in item:
            state = ((item["statusUpdate"].get("status") or {}).get("state")) or state
    return state, artifacts or answer


class _Emitter:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[str, BaseModel] | None] = asyncio.Queue()

    def emit(self, name: str, data: BaseModel) -> None:
        self.queue.put_nowait((name, data))

    def close(self) -> None:
        self.queue.put_nowait(None)


# --------------------------------------------------------------------------- one assistant turn


class AssistantRun:
    def __init__(
        self, user: CurrentUser, mandate: VerifiedMandate, token: str, messages: list[AssistantMessage]
    ):
        self.user = user
        self.mandate = mandate
        self.token = token
        self.messages = messages
        self.out = _Emitter()
        self.abort = asyncio.Event()
        self.turn_id = "t" + uuid.uuid4().hex[:24]
        self.request_id = "asst_" + uuid.uuid4().hex
        self.calls = 0
        self.tool_seq = 0
        self.discovered: dict[str, DiscoveredAgent] = {}
        self.settings = get_settings()

    # ---- frames

    def _tool_id(self, prefix: str) -> str:
        self.tool_seq += 1
        return f"{prefix}-{self.tool_seq}"

    async def _counters(self) -> tuple[int, int, int]:
        """``(spent_cents, budget_cents, calls)`` of the mandate (fresh read)."""
        try:
            snap = await get_db().collection(Col.MANDATES).document(self.mandate.mandate_id).get()
            data = snap.to_dict() or {}
        except Exception:
            log.warning("assistant_mandate_read_failed", exc_info=True)
            data = {}
        return (
            int(data.get("spent_cents", 0)),
            int(data.get("budget_cents", self.mandate.budget_cents)),
            int(data.get("calls", 0)),
        )

    async def _balance(self) -> int:
        try:
            return int(await _deps.billing().get_balance(self.user.uid))
        except Exception:
            log.warning("assistant_balance_read_failed", exc_info=True)
            return 0

    async def _emit_result(
        self,
        *,
        call_id: str | None,
        slug: str,
        status: Literal["completed", "failed", "rejected"],
        charged_cents: int = 0,
        creator_share_cents: int = 0,
        answer: str = "",
        error_code: str | None = None,
    ) -> None:
        spent, budget, _ = await self._counters()
        self.out.emit(
            EventName.A2A_RESULT,
            A2AResultData(
                call_id=call_id,
                slug=slug,
                status=status,
                charged_cents=charged_cents,
                creator_share_cents=creator_share_cents,
                answer_preview=_clip(answer, ANSWER_PREVIEW_CHARS),
                error_code=error_code,
                payer_balance_cents=await self._balance(),
                mandate_spent_cents=spent,
                mandate_budget_cents=budget,
            ),
        )

    # ---- tools

    async def find_agents(self, need: str) -> dict:
        """Find specialist agents in the TopSkill marketplace that can help with a need.

        Args:
            need: A short description of what the employee needs help with.

        Returns:
            Matching agents with slug, name, description, price (cents per call) and example requests.
        """
        tool_id = self._tool_id("find")
        self.out.emit(
            EventName.TOOL_START,
            ToolStartData(
                id=tool_id,
                tool="find_agents",
                kind="search",
                title="Searched the agent directory",
                target=_clip(need, 80),
            ),
        )
        try:
            ranked = await directory.search(need, limit=DISCOVER_TOP_N, fallback=True)
            async with http_client(self.settings.a2a_call_timeout_s) as http:
                cards = await asyncio.gather(*(self._fetch_card(http, a.card_url) for a in ranked))
            agents: list[dict] = []
            discovered: list[DiscoveredAgent] = []
            for item, card in zip(ranked, cards, strict=True):
                if card is None:
                    continue
                price = card_price_cents(card)
                price = item.price_cents if price is None else price
                skills = card.get("skills") or [{}]
                agents.append(
                    {
                        "slug": item.slug,
                        "name": card.get("name") or item.name,
                        "description": _clip(card.get("description") or item.tagline, 600),
                        "price_cents": price,
                        "examples": list(skills[0].get("examples") or [])[:3],
                    }
                )
                d = DiscoveredAgent(
                    slug=item.slug,
                    name=card.get("name") or item.name,
                    tagline=item.tagline,
                    price_cents=price,
                    card_url=item.card_url,
                )
                discovered.append(d)
                self.discovered[item.slug] = d
            self.out.emit(EventName.A2A_DISCOVER, A2ADiscoverData(query=_clip(need, 200), agents=discovered))
            self.out.emit(EventName.TOOL_END, ToolEndData(id=tool_id, tool="find_agents", status="completed"))
            return {"agents": agents}
        except Exception:
            log.exception("assistant_find_agents_failed")
            self.out.emit(EventName.TOOL_END, ToolEndData(id=tool_id, tool="find_agents", status="error"))
            return {"agents": [], "error": "The directory is unavailable right now."}

    async def _fetch_card(self, http: httpx.AsyncClient, url: str) -> dict | None:
        try:
            resp = await http.get(url)
            resp.raise_for_status()
            card = resp.json()
            return card if isinstance(card, dict) else None
        except Exception as exc:
            log.warning("assistant_card_fetch_failed url=%s error=%s", url, type(exc).__name__)
            return None

    async def call_agent(self, slug: str, request: str) -> dict:
        """Hire one specialist agent over A2A and get its answer. Each call is paid from the employee's
        authorised budget; a rejected or failed call is not charged.

        Args:
            slug: The agent slug returned by find_agents.
            request: A self-contained request with every fact the agent needs.

        Returns:
            ok, the agent's answer and the credits charged (cents), or an error_code.
        """
        self.calls += 1
        tool_id = self._tool_id("call")
        slug = (slug or "").strip().lower()
        agent: AgentDoc | None = None
        load_error: ApiError | None = None
        try:
            agent = await load_a2a_agent(slug)
        except ApiError as err:
            load_error = err
        name = agent.title if agent else (self.discovered[slug].name if slug in self.discovered else slug)
        preview = _clip(request or "", REQUEST_PREVIEW_CHARS)
        self.out.emit(
            EventName.TOOL_START,
            ToolStartData(
                id=tool_id, tool="call_agent", kind="a2a", title=f"Called {name}", target=slug[:80]
            ),
        )

        async def reject(
            code: str, *, price: int = 0, agent_name: str = name, call_sent: bool = True
        ) -> dict:
            if call_sent:
                self.out.emit(
                    EventName.A2A_CALL,
                    A2ACallData(
                        call_id=None,
                        slug=slug,
                        agent_name=agent_name,
                        endpoint=endpoint_url(slug),
                        price_cents=price,
                        request_preview=preview,
                    ),
                )
            await self._emit_result(call_id=None, slug=slug, status="rejected", error_code=code)
            self.out.emit(EventName.TOOL_END, ToolEndData(id=tool_id, tool="call_agent", status="error"))
            return {
                "ok": False,
                "error_code": code,
                "charged_cents": 0,
                "note": "Rejected before sending; not charged.",
            }

        # a. call limit
        if self.calls > MAX_CALLS_PER_TURN:
            return await reject(Codes.CALL_LIMIT, call_sent=False)
        if not (request or "").strip():
            return await reject(Codes.VALIDATION_ERROR)

        # b. in-process pre-check (no HTTP)
        if agent is None:
            assert load_error is not None
            log.info("assistant_call_rejected slug=%s code=%s", slug, load_error.code)
            return await reject(load_error.code)
        price = agent.pricing.a2a_price_cents
        try:
            mandate = await verify_token(self.token, slug)
            price = await _deps.billing().preflight_a2a(agent, mandate)
            if price > mandate.max_amount_cents:
                raise mandate_error(
                    Codes.MANDATE_EXCEEDS_MAX,
                    {"price_cents": price, "max_amount_cents": mandate.max_amount_cents},
                )
        except ApiError as err:
            log.info("assistant_call_rejected slug=%s code=%s", slug, err.code)
            return await reject(err.code, price=price, agent_name=name)

        # c. send over HTTP
        message_id = uuid.uuid4().hex
        call_id = call_id_for(mandate.mandate_id, message_id)
        self.out.emit(
            EventName.A2A_CALL,
            A2ACallData(
                call_id=call_id,
                slug=slug,
                agent_name=name,
                endpoint=endpoint_url(slug),
                price_cents=price,
                request_preview=preview,
            ),
        )
        state, answer, error_code, http_status = "", "", None, None
        try:
            responses = await self._send(slug, request, message_id)
            state, answer = extract_result(responses)
        except A2AClientError as err:
            error_code, http_status = a2a_error_code(err)
        except Exception as exc:
            log.warning("assistant_call_failed slug=%s error=%s", slug, type(exc).__name__)
            error_code = Codes.UPSTREAM_ERROR

        call = await self._read_call(call_id)
        charged = int(call.get("charged_cents", 0)) if call else 0
        share = int(call.get("creator_share_cents", 0)) if call else 0
        if error_code is None and (state not in ("TASK_STATE_COMPLETED", "message") or not answer.strip()):
            error_code = Codes.AGENT_RUN_FAILED
        if error_code is None and call is not None and call.get("status") == "failed":
            error_code = call.get("error_code") or Codes.AGENT_RUN_FAILED

        if error_code is not None:
            status: Literal["failed", "rejected"] = (
                "rejected" if http_status is not None and 400 <= http_status < 500 else "failed"
            )
            await self._emit_result(
                call_id=call_id,
                slug=slug,
                status=status,
                charged_cents=charged,
                creator_share_cents=share,
                error_code=error_code,
            )
            self.out.emit(EventName.TOOL_END, ToolEndData(id=tool_id, tool="call_agent", status="error"))
            return {"ok": False, "error_code": error_code, "charged_cents": charged}

        await self._emit_result(
            call_id=call_id,
            slug=slug,
            status="completed",
            charged_cents=charged,
            creator_share_cents=share,
            answer=answer,
        )
        self.out.emit(EventName.TOOL_END, ToolEndData(id=tool_id, tool="call_agent", status="completed"))
        return {"ok": True, "agent": name, "answer": answer[:ANSWER_TO_MODEL_CHARS], "charged_cents": charged}

    async def _send(self, slug: str, request: str, message_id: str) -> list[dict]:
        out: list[dict] = []
        async with http_client(self.settings.a2a_call_timeout_s) as http:
            client = await create_client(
                endpoint_url(slug), client_config=ClientConfig(streaming=False, httpx_client=http)
            )
            try:
                req = SendMessageRequest(
                    message=Message(message_id=message_id, role=Role.ROLE_USER, parts=[Part(text=request)])
                )
                ctx = ClientCallContext(service_parameters={"X-AP2-Mandate": self.token})
                async with asyncio.timeout(self.settings.a2a_call_timeout_s):
                    async for item in client.send_message(req, context=ctx):
                        msg = item[0] if isinstance(item, tuple) else item
                        out.append(MessageToDict(msg))
            finally:
                await client.close()
        return out

    async def _read_call(self, call_id: str) -> dict | None:
        try:
            snap = await get_db().collection(Col.A2A_CALLS).document(call_id).get()
            return snap.to_dict() if snap.exists else None
        except Exception:
            log.warning("assistant_call_read_failed call_id=%s", call_id, exc_info=True)
            return None

    # ---- the turn

    async def _seed(self, runner: Runner, session_id: str) -> None:
        session = await runner.session_service.get_session(
            app_name=APP_NAME, user_id=self.user.uid, session_id=session_id
        )
        assert session is not None
        for i, msg in enumerate(self.messages[:-1]):
            if not msg.text.strip():
                continue
            await runner.session_service.append_event(
                session,
                Event(
                    invocation_id=f"seed-{i}",
                    author="user" if msg.role == "user" else AGENT_NAME,
                    content=types.Content(role=msg.role, parts=[types.Part(text=msg.text)]),
                ),
            )

    async def produce(self) -> None:
        settings = self.settings
        answer: list[str] = []
        error: tuple[str, str] | None = None
        runner_finished = False
        usage = DoneUsage(request_id=self.request_id, input_tokens=None, output_tokens=None, model=None)
        try:
            chain = model_chain("assistant")
            self.out.emit(
                EventName.STATUS,
                StatusData(
                    conversation_id=None, turn_id=self.turn_id, request_id=self.request_id, model=chain[0]
                ),
            )
            llm_agent = LlmAgent(
                name=AGENT_NAME,
                model=build_model(),
                instruction=ASSISTANT_PROMPT,
                tools=[self.find_agents, self.call_agent],
                generate_content_config=types.GenerateContentConfig(
                    thinking_config=portable_thinking_config("low")
                ),
            )
            runner = Runner(app_name=APP_NAME, agent=llm_agent, session_service=InMemorySessionService())
            session = await runner.session_service.create_session(app_name=APP_NAME, user_id=self.user.uid)
            await self._seed(runner, session.id)
            run_config = RunConfig(
                streaming_mode=StreamingMode.SSE, max_llm_calls=settings.max_llm_calls_per_turn
            )
            agen = runner.run_async(
                user_id=self.user.uid,
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=self.messages[-1].text)]),
                run_config=run_config,
            )
            partial_seen = False
            try:
                async with asyncio.timeout(settings.turn_total_timeout_s):
                    async for ev in agen:
                        if self.abort.is_set():
                            break
                        if ev.error_code and ev.error_code != "STOP":
                            log.warning(
                                "assistant_model_error code=%s message=%s", ev.error_code, ev.error_message
                            )
                            error = (Codes.AGENT_RUN_FAILED, "The assistant could not finish this answer.")
                            break
                        if ev.usage_metadata is not None:
                            usage = DoneUsage(
                                request_id=self.request_id,
                                input_tokens=ev.usage_metadata.prompt_token_count,
                                output_tokens=ev.usage_metadata.candidates_token_count,
                                model=ev.model_version or usage.model,
                            )
                        text = "".join(
                            p.text
                            for p in (ev.content.parts if ev.content else None) or []
                            if p.text and not p.thought
                        )
                        if ev.partial:
                            if text:
                                partial_seen = True
                                answer.append(text)
                                self.out.emit(EventName.TEXT_DELTA, TextDeltaData(delta=text))
                            continue
                        if text and not partial_seen and ev.author == AGENT_NAME:
                            answer.append(text)
                            self.out.emit(EventName.TEXT_DELTA, TextDeltaData(delta=text))
                        partial_seen = False
                    else:
                        runner_finished = True
            finally:
                with contextlib.suppress(Exception):
                    await agen.aclose()
            if error is not None:
                runner_finished = True
        except TimeoutError:
            runner_finished = True
            error = (Codes.TURN_TIMEOUT, "The assistant took too long to answer.")
        except ApiError as err:
            runner_finished = True
            error = (err.code, err.message)
        except Exception as exc:
            runner_finished = True
            log.exception("assistant_run_failed")
            if is_retriable(exc) and getattr(exc, "code", None) == 429:
                error = (Codes.RATE_LIMITED, "The assistant is busy. Please try again.")
            else:
                error = (Codes.AGENT_RUN_FAILED, "The assistant could not finish this answer.")
        finally:
            text = "".join(answer)
            if self.abort.is_set() and not runner_finished:
                outcome: Literal["ok", "failed", "aborted"] = "aborted"
            elif not text.strip():
                outcome = "failed"
            else:
                outcome = "ok"
            if error is not None:
                self.out.emit(
                    EventName.ERROR,
                    StreamErrorData(code=error[0], message=error[1], trace_id=current_trace_id()),
                )
            spent, budget, calls = await self._counters()
            self.out.emit(
                EventName.DONE,
                AssistantDoneData(
                    conversation_id=None,
                    message_id=None,
                    turn_id=self.turn_id,
                    outcome=outcome,
                    usage=usage,
                    mandate=MandateCounters(
                        id=self.mandate.mandate_id, spent_cents=spent, budget_cents=budget, calls=calls
                    ),
                ),
            )
            self.out.close()

    async def events(self) -> AsyncIterator[tuple[str, BaseModel]]:
        tasks.spawn(self.produce(), name=f"assistant:{self.request_id}")
        try:
            while True:
                item = await self.out.queue.get()
                if item is None:
                    return
                yield item
        finally:
            self.abort.set()  # client went away (or done): stop the runner between events


async def start(body: AssistantRequest, user: CurrentUser) -> StreamingResponse:
    """Verify the mandate (402 before streaming), then stream the assistant turn (SSE §7.5)."""
    mandate = await verify_token(body.mandate_token, None)
    if mandate.payer_uid != user.uid:
        raise ApiError(403, Codes.FORBIDDEN, "This mandate belongs to another account.")
    await ratelimit.hit("assistant", user.uid, limit=RATE_LIMIT_PER_HOUR, window="hour")
    run = AssistantRun(user, mandate, body.mandate_token.strip(), body.messages)
    return sse_response(run.events())
