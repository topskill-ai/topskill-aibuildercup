"""A2A routes (SPEC §6.4, §9.2, §9.3): Agent Cards, the JSON-RPC endpoint, the directory and the
Enterprise Assistant demo stream."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from topskill import repo, tasks
from topskill.auth import CurrentUser, get_current_user
from topskill.errors import ApiError, Codes, current_trace_id

from . import _deps, assistant, directory
from .cards import build_card, card_to_dict
from .executor import get_stack, is_call_active, load_a2a_agent, settle_call
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
    texts = [p.get("text") for p in message.get("parts") or [] if isinstance(p, dict)]
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


@router.get("/a2a/{slug}/.well-known/agent-card.json")
async def get_agent_card(slug: str) -> JSONResponse:
    agent = await load_a2a_agent(slug)
    version = await repo.get_active_version(agent)
    return JSONResponse(card_to_dict(build_card(agent, version)))


@router.post("/a2a/{slug}")
async def a2a_rpc(slug: str, request: Request) -> Response:
    try:
        body = await request.json()  # Starlette caches the parsed body; the dispatcher reads the same dict
    except (json.JSONDecodeError, UnicodeDecodeError):
        return rpc_error(None, -32700, 400, Codes.VALIDATION_ERROR, "Parse error.")
    if not isinstance(body, dict):  # JSON-RPC batch arrays are not supported
        return rpc_error(None, -32600, 400, Codes.VALIDATION_ERROR, "Invalid request.")
    rpc_id, method, params = body.get("id"), body.get("method"), body.get("params")
    if not isinstance(params, dict):
        return rpc_error(rpc_id, -32602, 400, Codes.VALIDATION_ERROR, "Invalid params.")
    try:
        agent = await load_a2a_agent(slug)
    except ApiError as err:
        return api_error_rpc(rpc_id, err)
    if method not in PAID | READ:
        return rpc_error(rpc_id, -32601, 404, Codes.NOT_FOUND, "Method not found.")

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
            stack = await get_stack(slug, agent)
        except ApiError as err:
            return api_error_rpc(rpc_id, err)
        return await stack.dispatcher.handle_requests(request)

    message = params.get("message")
    message_id = message.get("messageId") if isinstance(message, dict) else None
    if not isinstance(message_id, str) or not message_id or len(message_id) > 200:
        return rpc_error(rpc_id, -32602, 400, Codes.VALIDATION_ERROR, "Invalid params: message.messageId.")
    _force_blocking(method, params)
    try:
        stack = await get_stack(slug, agent)  # before reserving: a build failure must not hold budget
        price = await _deps.billing().preflight_a2a(agent, mandate)
        call_id = await reserve_call(mandate, agent, message_id, request_preview(message), price)
    except ApiError as err:
        return api_error_rpc(rpc_id, err)
    request.state.a2a_call_id = call_id
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


@router.get("/api/public/a2a/directory", response_model=dict)
async def a2a_directory(q: str | None = Query(None, max_length=500)) -> dict:
    items = await directory.search(q)
    return {"items": [item.model_dump() for item in items]}


@router.post("/api/demo/assistant/chat")
async def assistant_chat(
    body: assistant.AssistantRequest, user: CurrentUser = Depends(get_current_user)
) -> Response:
    return await assistant.start(body, user)
