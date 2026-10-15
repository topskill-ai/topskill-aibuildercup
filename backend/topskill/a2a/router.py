"""A2A routes (SPEC §6.4, §9.2, §9.3): Agent Cards, the JSON-RPC endpoint, the directory and the
Enterprise Assistant demo stream."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from topskill import guard, repo, tasks
from topskill.auth import CurrentUser, get_current_user
from topskill.domain import AgentDoc
from topskill.errors import ApiError, Codes, current_trace_id
from topskill.observability import note_a2a_call

from . import _deps, assistant, directory
from .cards import build_card, card_to_dict
from .directory import DirectoryAgent
from .executor import (
    DEADLINE_STATE_KEY,
    A2AStack,
    get_stack,
    is_call_active,
    load_a2a_agent,
    run_budget_s,
    settle_call,
)
from .mandates import reserve_call, verify_token

log = logging.getLogger(__name__)

router = APIRouter(tags=["a2a"])

PAID = frozenset({"SendMessage", "SendStreamingMessage", "message/send", "message/stream"})
READ = frozenset(
    {
        "GetTask",
        "CancelTask",
        "ListTasks",
        "SubscribeToTask",
        "tasks/get",
        "tasks/cancel",
        "tasks/resubscribe",
    }
)
V1_PAID = frozenset({"SendMessage", "SendStreamingMessage"})
# A paid task cannot be canceled: once its answer text has streamed (or is readable through GetTask), a
# cancel would settle the call as failed and hand the payer a free answer (SPEC §9.3).
CANCEL = frozenset({"CancelTask", "tasks/cancel"})
TASK_NOT_CANCELABLE = -32002  # A2A TaskNotCancelableError
REQUEST_PREVIEW_CHARS = 120


def rpc_error(
    rpc_id: Any, code: int, status: int, data_code: str, message: str, detail: dict | None = None
) -> JSONResponse:
    """JSON-RPC error body with our error code, trace id and detail in ``error.data`` (SPEC §9.3)."""
    if rpc_id is not None and (isinstance(rpc_id, bool) or not isinstance(rpc_id, str | int)):
        rpc_id = None
    body = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {
            "code": code,
            "message": message,
            "data": {"code": data_code, "trace_id": current_trace_id(), "detail": detail or {}},
        },
    }
    return JSONResponse(body, status_code=status)


def api_error_rpc(rpc_id: Any, err: ApiError) -> JSONResponse:
    return rpc_error(rpc_id, -32001, err.status, err.code, err.message, err.detail)


def request_preview(message: dict) -> str:
    """Text of the A2A message parts (v1.0 ``{text}`` and v0.3 ``{kind: "text", text}``), ≤ 120 chars."""
    parts = message.get("parts")
    if not isinstance(parts, list):
        parts = []
    texts = [p.get("text") for p in parts if isinstance(p, dict)]
    joined = " ".join(" ".join(t.split()) for t in texts if isinstance(t, str) and t.strip())
    return joined[:REQUEST_PREVIEW_CHARS]


async def settle_failed_if_running(call_id: str) -> None:
    """Settle a reserved call as failed unless its executor is still running (it settles itself).
    A no-op for calls the executor already settled (``settle_a2a_call`` is idempotent)."""
    if is_call_active(call_id):
        return
    try:
        await settle_call(
            call_id, succeeded=False, model=None, answer_preview="", error_code=Codes.UPSTREAM_ERROR
        )
    except Exception:
        log.exception("a2a_route_settle_failed call_id=%s", call_id)


async def _settle_after(body_iterator: AsyncIterator[Any], call_id: str) -> AsyncIterator[Any]:
    try:
        async for chunk in body_iterator:
            yield chunk
    finally:
        # The stream ended or the client went away (this generator may be cancelled): settle in a
        # tracked task so the check always runs.
        tasks.spawn(settle_failed_if_running(call_id), name=f"a2a-stream-settle:{call_id}")


def _force_blocking(method: str, params: dict) -> None:
    """Paid calls always wait for the result: ``returnImmediately`` would let the executor run on after
    the response, past the route's settlement check."""
    config = params.get("configuration")
    if not isinstance(config, dict):
        return
    if method in V1_PAID:
        config.pop("returnImmediately", None)
        config.pop("return_immediately", None)
    else:
        config.pop("blocking", None)


async def _stack(slug: str, agent: AgentDoc) -> A2AStack:
    """The cached stack; a build failure (e.g. a dependency that cannot load) becomes 503."""
    try:
        return await get_stack(slug, agent)
    except ApiError:
        raise
    except Exception as exc:
        log.exception("a2a_stack_build_failed slug=%s", slug)
        raise ApiError(503, Codes.SERVICE_UNAVAILABLE, "This agent is temporarily unavailable.") from exc


async def settle_blocked(call_id: str) -> None:
    """Settle a call blocked by the guard as failed (never charged)."""
    try:
        await settle_call(
            call_id, succeeded=False, model=None, answer_preview="", error_code=Codes.BLOCKED_BY_POLICY
        )
    except Exception:
        log.exception("a2a_blocked_settle_failed call_id=%s", call_id)
        await settle_failed_if_running(call_id)


class DirectoryResponse(BaseModel):
    items: list[DirectoryAgent]


@router.get("/a2a/{slug}/.well-known/agent-card.json")
async def get_agent_card(slug: str) -> JSONResponse:
    agent = await load_a2a_agent(slug)
    version = await repo.get_active_version(agent)
    return JSONResponse(card_to_dict(build_card(agent, version)))


@router.post("/a2a/{slug}")
async def a2a_rpc(slug: str, request: Request) -> Response:
    # The run budget counts from here, so preflight time cannot push the run past the caller's timeout.
    deadline = asyncio.get_running_loop().time() + run_budget_s()
    try:
        body = await request.json()  # Starlette caches the parsed body; the dispatcher reads the same dict
    except (json.JSONDecodeError, UnicodeDecodeError):
        return rpc_error(None, -32700, 400, Codes.VALIDATION_ERROR, "Parse error.")
    if not isinstance(body, dict):  # JSON-RPC batch arrays are not supported
        return rpc_error(None, -32600, 400, Codes.VALIDATION_ERROR, "Invalid request.")
    rpc_id, method, params = body.get("id"), body.get("method"), body.get("params")
    if not isinstance(method, str) or not method:
        return rpc_error(rpc_id, -32600, 400, Codes.VALIDATION_ERROR, "Invalid request.")
    if not isinstance(params, dict):
        return rpc_error(rpc_id, -32602, 400, Codes.VALIDATION_ERROR, "Invalid params.")
    try:
        agent = await load_a2a_agent(slug)
    except ApiError as err:
        return api_error_rpc(rpc_id, err)
    if method not in PAID | READ:
        return rpc_error(rpc_id, -32601, 404, Codes.NOT_FOUND, "Method not found.")
    if method in CANCEL:
        return rpc_error(
            rpc_id, TASK_NOT_CANCELABLE, 409, Codes.VALIDATION_ERROR, "Paid A2A tasks cannot be canceled."
        )

    metadata = params.get("metadata")
    header_token = request.headers.get("x-ap2-mandate")
    meta_token = metadata.get("ap2_mandate") if isinstance(metadata, dict) else None
    token = header_token or meta_token
    if isinstance(metadata, dict):
        metadata.pop("ap2_mandate", None)  # the bearer token never reaches the A2A layer
    try:
        mandate = await verify_token(token if isinstance(token, str) else None, slug)
    except ApiError as err:
        return api_error_rpc(rpc_id, err)
    request.state.verified_mandate = mandate

    if method in READ:  # verified, never reserved or charged
        try:
            stack = await _stack(slug, agent)
        except ApiError as err:
            return api_error_rpc(rpc_id, err)
        return await stack.dispatcher.handle_requests(request)

    message = params.get("message")
    message_id = message.get("messageId") if isinstance(message, dict) else None
    if not isinstance(message_id, str) or not message_id or len(message_id) > 200:
        return rpc_error(rpc_id, -32602, 400, Codes.VALIDATION_ERROR, "Invalid params: message.messageId.")
    if not isinstance(message.get("parts"), list) or not message["parts"]:
        return rpc_error(rpc_id, -32602, 400, Codes.VALIDATION_ERROR, "Invalid params: message.parts.")
    _force_blocking(method, params)
    # P2 guard (SPEC §18 "Guard"): screen every part of the caller's message during the preflight (redacted
    # in place); never raises. A part that cannot be screened fails closed before anything is reserved. A
    # blocked message is still reserved and then settled as failed, so the call shows up (not charged).
    prompt_check = asyncio.ensure_future(
        guard.screen_a2a_message(agent.id, agent.config.safety_screening, message)
    )
    try:
        stack = await _stack(slug, agent)  # before reserving: a build failure must not hold budget
        price = await _deps.billing().preflight_a2a(agent, mandate)
        screen = await prompt_check  # before the preview is stored: it must already be redacted
        if screen.invalid:
            return rpc_error(
                rpc_id,
                -32602,
                400,
                Codes.VALIDATION_ERROR,
                f"Invalid params: message.parts ({screen.invalid}; this agent screens messages and accepts "
                "text, JSON data and text files only).",
            )
        verdict = screen.verdict
        call_id = await reserve_call(mandate, agent, message_id, request_preview(message), price)
    except ApiError as err:
        prompt_check.cancel()
        return api_error_rpc(rpc_id, err)
    except Exception:  # nothing was reserved (T8 is all-or-nothing)
        prompt_check.cancel()
        log.exception("a2a_preflight_failed slug=%s", slug)
        return rpc_error(
            rpc_id, -32001, 503, Codes.SERVICE_UNAVAILABLE, "This agent is temporarily unavailable."
        )
    if verdict.blocked:
        await settle_blocked(call_id)
        log.info(
            "a2a_call_blocked slug=%s call_id=%s categories=%s", slug, call_id, ",".join(verdict.categories)
        )
        return rpc_error(
            rpc_id,
            -32001,
            400,
            Codes.BLOCKED_BY_POLICY,
            "The message was blocked by the agent's safety screening. The call was not charged.",
            {"categories": list(verdict.categories), "call_id": call_id},
        )
    request.state.a2a_call_id = call_id
    note_a2a_call(  # P2 §18.3: attributes for the executor's call span
        agent_id=agent.id,
        slug=slug,
        call_id=call_id,
        price_cents=price,
        method=method,
        payer_uid=mandate.payer_uid,
    )
    setattr(request.state, DEADLINE_STATE_KEY, deadline)
    log.info("a2a_call_reserved slug=%s call_id=%s price_cents=%s method=%s", slug, call_id, price, method)
    try:
        resp = await stack.dispatcher.handle_requests(request)
    except BaseException:
        await settle_failed_if_running(call_id)
        raise
    body_iterator = getattr(resp, "body_iterator", None)
    if body_iterator is not None:  # SSE (SendStreamingMessage)
        resp.body_iterator = _settle_after(body_iterator, call_id)  # type: ignore[attr-defined]
    else:  # dispatcher rejected the params, or execute finished / never ran
        await settle_failed_if_running(call_id)
    return resp


@router.get("/api/public/a2a/directory", response_model=DirectoryResponse)
async def a2a_directory(q: str | None = Query(None, max_length=500)) -> DirectoryResponse:
    return DirectoryResponse(items=await directory.search(q))


@router.post("/api/demo/assistant/chat")
async def assistant_chat(
    body: assistant.AssistantRequest, user: CurrentUser = Depends(get_current_user)
) -> Response:
    return await assistant.start(body, user)
