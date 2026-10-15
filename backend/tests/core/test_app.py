"""App-level behaviour: envelope, trace ids, CORS, body limit, /healthz, /api/public/meta."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from topskill.errors import (
    ERROR_CODES,
    ApiError,
    BodySizeLimitMiddleware,
    Codes,
    TraceIdMiddleware,
    current_trace_id,
    install_error_handlers,
)
from topskill.main import create_app

pytestmark = pytest.mark.unit


class Body(BaseModel):
    n: int


def build_app() -> FastAPI:
    app = create_app()

    @app.get("/test/api-error")
    async def api_error() -> dict:
        raise ApiError(
            402,
            Codes.VISITOR_INSUFFICIENT_POINTS,
            "You need 1.00 credits; you have 0.40.",
            {"required_cents": 100, "balance_cents": 40},
        )

    @app.get("/test/boom")
    async def boom() -> dict:
        raise RuntimeError("secret internals")

    @app.post("/test/validate")
    async def validate(body: Body) -> dict:
        return {"n": body.n}

    @app.get("/test/trace")
    async def trace() -> dict:
        return {"trace_id": current_trace_id()}

    @app.post("/test/upload")
    async def upload(body: dict) -> dict:
        return {"ok": True}

    return app


@pytest.fixture(scope="module")
async def c():
    transport = httpx.ASGITransport(app=build_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def assert_envelope(resp: httpx.Response, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"code", "message", "trace_id", "detail"}
    assert body["code"] == code
    assert body["trace_id"] and resp.headers["x-trace-id"] == body["trace_id"]
    return body


async def test_api_error_envelope(c: httpx.AsyncClient) -> None:
    body = assert_envelope(await c.get("/test/api-error"), 402, "visitor_insufficient_points")
    assert body["detail"] == {"required_cents": 100, "balance_cents": 40}
    assert body["message"] == "You need 1.00 credits; you have 0.40."


async def test_validation_error_is_400(c: httpx.AsyncClient) -> None:
    body = assert_envelope(await c.post("/test/validate", json={"n": "x"}), 400, "validation_error")
    errors = body["detail"]["errors"]
    assert errors and errors[0]["loc"] == ["body", "n"] and isinstance(errors[0]["msg"], str)


async def test_invalid_json_is_400(c: httpx.AsyncClient) -> None:
    resp = await c.post("/test/validate", content=b"{", headers={"content-type": "application/json"})
    assert_envelope(resp, 400, "validation_error")


async def test_unhandled_exception_is_500_envelope(c: httpx.AsyncClient) -> None:
    body = assert_envelope(await c.get("/test/boom"), 500, "internal_error")
    assert "secret" not in body["message"]


async def test_unknown_route_and_method(c: httpx.AsyncClient) -> None:
    assert_envelope(await c.get("/nope"), 404, "not_found")
    assert_envelope(await c.delete("/healthz"), 405, "not_found")


async def test_trace_id_from_cloud_trace_header(c: httpx.AsyncClient) -> None:
    resp = await c.get(
        "/test/trace", headers={"X-Cloud-Trace-Context": "105445aa7843bc8bf206b12000100000/1;o=1"}
    )
    assert resp.json()["trace_id"] == "105445aa7843bc8bf206b12000100000"
    assert resp.headers["x-trace-id"] == "105445aa7843bc8bf206b12000100000"


async def test_trace_id_generated_and_unique(c: httpx.AsyncClient) -> None:
    a = (await c.get("/test/trace")).json()["trace_id"]
    b = (await c.get("/test/trace")).json()["trace_id"]
    assert len(a) == 32 and a != b


async def test_body_limit_413() -> None:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=100)
    app.add_middleware(TraceIdMiddleware)

    @app.post("/upload")
    async def upload(body: dict) -> dict:
        return {"ok": True}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ok = await client.post("/upload", json={"a": "x" * 10})
        assert ok.status_code == 200
        too_big = await client.post("/upload", json={"a": "x" * 200})
        body = assert_envelope(too_big, 413, "payload_too_large")
        assert body["detail"] == {"max_bytes": 100}

        async def chunks():
            for _ in range(5):
                yield b"x" * 40

        streamed = await client.post(
            "/upload", content=chunks(), headers={"content-type": "application/json"}
        )
        assert_envelope(streamed, 413, "payload_too_large")


async def test_health_alias_matches_healthz(c: httpx.AsyncClient) -> None:
    health = await c.get("/health")
    healthz = await c.get("/healthz")
    assert health.status_code == healthz.status_code == 200
    assert health.json() == healthz.json()


async def test_healthz(c: httpx.AsyncClient) -> None:
    resp = await c.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "env": "test", "version": "test"}


async def test_meta_shape(c: httpx.AsyncClient) -> None:
    resp = await c.get("/api/public/meta")
    assert resp.status_code == 200
    assert resp.json() == {
        "chat_models": [
            "gemini-3.8-flash",
            "gemini-3-flash-preview",
            "gemini-3.1-pro-preview",
            "gemini-2.5-pro",
        ],
        "default_chat_model": "gemini-3.8-flash",
        "revenue_share_bp": 8000,
        "signup_grant_cents": 10000,
        "demo_topup_cents": 5000,
        "demo_topup_daily_limit": 5,
        "a2a_mandate_defaults": {
            "max_amount_cents": 500,
            "budget_cents": 2000,
            "ttl_minutes": 30,
            "max_budget_cents": 100000,
            "max_ttl_minutes": 1440,
        },
    }


async def test_cors_preflight_preview_channel(c: httpx.AsyncClient) -> None:
    origin = "https://top-skill--pr-12-abc123.web.app"
    resp = await c.options(
        "/api/me",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,x-ap2-mandate",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == origin
    assert "authorization" in resp.headers["access-control-allow-headers"].lower()
    assert resp.headers.get("access-control-allow-credentials") is None


async def test_cors_allowed_origin_and_exposed_headers(c: httpx.AsyncClient) -> None:
    resp = await c.get("/healthz", headers={"Origin": "http://localhost:5173"})
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "x-trace-id" in resp.headers["access-control-expose-headers"].lower()


async def test_cors_rejects_unknown_origin(c: httpx.AsyncClient) -> None:
    resp = await c.options(
        "/api/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in resp.headers


async def test_cors_headers_on_500(c: httpx.AsyncClient) -> None:
    resp = await c.get("/test/boom", headers={"Origin": "http://localhost:5173"})
    assert resp.status_code == 500
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_app_boots_with_every_module() -> None:
    app = create_app()
    assert app.state.mounted_routers == [
        "topskill.users.router:router",
        "topskill.meta_router:router",
        "topskill.skills.router:router",
        "topskill.agents.router:router",
        "topskill.agents.public_router:router",
        "topskill.billing.router:router",
        "topskill.billing.router:public_router",
        "topskill.knowledge.router:router",
        "topskill.records.router:router",
        "topskill.chat.router:public_router",
        "topskill.chat.router:router",
        "topskill.a2a.mandate_router:router",
        "topskill.a2a.router:router",
    ]
    spec = app.openapi()
    assert {
        "/healthz",
        "/api/me",
        "/api/me/become-creator",
        "/api/public/meta",
        "/api/skills",
        "/api/agents",
        "/api/public/agents/{slug}",
        "/api/wallet",
        "/api/dashboard",
        "/api/agents/{agent_id}/knowledge",
        "/api/agents/{agent_id}/records",
        "/api/public/agents/{slug}/chat",
        "/api/mandates",
        "/api/demo/assistant/chat",
    } <= set(spec["paths"])
    ops = {op["operationId"] for item in spec["paths"].values() for op in item.values()}
    assert {"healthz", "get_me", "update_me", "become_creator", "get_meta"} <= ops
    assert "/health" not in spec["paths"]  # external alias of /healthz, hidden from the schema


def test_error_codes_cover_spec() -> None:
    for code in (
        "validation_error",
        "mandate_replay",
        "session_not_owned",
        "turn_timeout",
        "call_limit",
        "connection_lost",
        "network_error",
        "image_failed",
        "draft_failed",
        "record_field_type_locked",
    ):
        assert code in ERROR_CODES
