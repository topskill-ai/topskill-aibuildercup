"""Request converter limits and the A2A runtime agent's thinking config (SPEC §9.1, §10.4)."""

from __future__ import annotations

import httpx
import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Message, Part, Role, SendMessageRequest
from starlette.requests import Request

from topskill.a2a.assistant import a2a_error_code, extract_result
from topskill.a2a.executor import MandateContextBuilder, PayerUser, limited_request_converter
from topskill.db import utcnow
from topskill.domain import VerifiedMandate


def _context(metadata: dict) -> RequestContext:
    req = SendMessageRequest(
        message=Message(message_id="m1", role=Role.ROLE_USER, parts=[Part(text="hello")])
    )
    req.metadata.update(metadata)
    return RequestContext(ServerCallContext(user=PayerUser("payer-1")), request=req)


def test_converter_strips_mandate_and_limits_llm_calls() -> None:
    run_request = limited_request_converter(
        _context({"ap2_mandate": "secret-token", "trace": "abc"}), lambda p: []
    )
    assert run_request.run_config.max_llm_calls == 8
    assert run_request.run_config.custom_metadata == {"a2a_metadata": {"trace": "abc"}}
    assert run_request.user_id == "a2a:payer-1"
    assert "secret-token" not in run_request.model_dump_json()

    only_mandate = limited_request_converter(_context({"ap2_mandate": "secret-token"}), lambda p: [])
    assert not only_mandate.run_config.custom_metadata
    assert only_mandate.run_config.max_llm_calls == 8


def test_context_builder_sets_payer_and_drops_mandate_header() -> None:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/a2a/x",
        "headers": [(b"x-ap2-mandate", b"secret-token"), (b"content-type", b"application/json")],
        "state": {},
    }
    request = Request(scope)
    request.state.verified_mandate = VerifiedMandate(
        mandate_id="mdt_1",
        payer_uid="payer-1",
        audience="*",
        intent="i",
        max_amount_cents=1,
        budget_cents=1,
        expires_at=utcnow(),
        kid="dev-1",
        payer_agent=None,
    )
    request.state.a2a_call_id = "c" * 32
    ctx = MandateContextBuilder().build(request)
    assert ctx.user.is_authenticated and ctx.user.user_name == "a2a:payer-1"
    assert ctx.state["a2a_call_id"] == "c" * 32 and ctx.state["verified_mandate"].mandate_id == "mdt_1"
    assert "x-ap2-mandate" not in ctx.state["headers"] and ctx.state["headers"]["content-type"]


def test_a2a_error_code_mapping() -> None:
    req = httpx.Request("POST", "http://x/a2a/s")
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32001, "message": "m", "data": {"code": "mandate_exceeds_max"}},
    }
    resp = httpx.Response(402, json=body, request=req)
    from a2a.client import A2AClientError

    try:
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as cause:
            raise A2AClientError(f"HTTP Error 402: {cause}") from cause
    except A2AClientError as err:
        assert a2a_error_code(err) == ("mandate_exceeds_max", 402)
    assert a2a_error_code(A2AClientError("HTTP Error 410: gone")) == ("agent_offline", 410)
    assert a2a_error_code(A2AClientError("Network communication error")) == ("upstream_error", None)


def test_extract_result_shapes() -> None:
    task = {
        "task": {
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"parts": [{"text": "A"}, {"text": "B"}]}],
        }
    }
    assert extract_result([task]) == ("TASK_STATE_COMPLETED", "AB")
    failed = {"task": {"status": {"state": "TASK_STATE_FAILED", "message": {"parts": [{"text": "boom"}]}}}}
    assert extract_result([failed]) == ("TASK_STATE_FAILED", "boom")
    stream = [
        {"task": {"status": {"state": "TASK_STATE_SUBMITTED"}}},
        {"artifactUpdate": {"artifact": {"parts": [{"text": "X"}]}}},
        {"statusUpdate": {"status": {"state": "TASK_STATE_COMPLETED"}}},
    ]
    assert extract_result(stream) == ("TASK_STATE_COMPLETED", "X")
    assert extract_result([{"message": {"parts": [{"text": "direct"}]}}]) == ("message", "direct")


@pytest.mark.emulator
async def test_a2a_runtime_agent_uses_portable_thinking_budget() -> None:
    from topskill.chat import build_runtime_agent
    from topskill.repo import get_active_version
    from topskill.testing.factories import make_agent, make_user

    owner = await make_user("creator")
    agent = await make_agent(owner)
    llm_agent = await build_runtime_agent(agent, await get_active_version(agent), channel="a2a")
    thinking = llm_agent.generate_content_config.thinking_config
    assert thinking.thinking_budget is not None and thinking.thinking_level is None
