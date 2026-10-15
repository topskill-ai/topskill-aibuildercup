"""Tracing on (SPEC §18.3) must not break requests that only partially match a route: CORS preflights
(``OPTIONS`` on a ``GET`` route) and wrong-method calls. FastAPI ≥ 0.141 nests included routers under
``_IncludedRouter`` nodes without a ``path``; an instrumentation that reads ``route.path`` on a partial
match answers every preflight with a 500 (seen in prod on 2026-10-15, revision 00006)."""

from __future__ import annotations

import httpx

from topskill.observability.tracing import instrument_app

ORIGIN = "http://localhost:5173"


async def _client(otel) -> httpx.AsyncClient:
    from topskill.main import create_app

    app = create_app()
    instrument_app(app, otel.provider)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_cors_preflight_succeeds_with_tracing(otel) -> None:
    async with await _client(otel) as client:
        resp = await client.options(
            "/api/me",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.headers["access-control-allow-origin"] == ORIGIN


async def test_wrong_method_is_405_not_500_with_tracing(otel) -> None:
    async with await _client(otel) as client:
        resp = await client.delete("/api/public/agents")
    assert resp.status_code == 405, resp.text
    server = [s for s in otel.spans() if s.kind.name == "SERVER"]
    assert [s.name for s in server] == ["DELETE /api/public/agents"]


def test_span_name_at_start_resolves_included_routes() -> None:
    from topskill.main import create_app
    from topskill.observability.tracing import _route_details_at_start

    app = create_app()

    def scope(method: str, path: str) -> dict:
        return {"type": "http", "app": app, "method": method, "path": path, "root_path": "", "headers": []}

    assert (
        _route_details_at_start(scope("GET", "/api/public/agents/visa-advisor"))
        == "/api/public/agents/{slug}"
    )
    assert _route_details_at_start(scope("OPTIONS", "/api/me")) == "/api/me"  # partial match
    assert _route_details_at_start(scope("GET", "/no/such/route")) is None
    assert _route_details_at_start({"type": "http", "path": "/x"}) is None  # no app: never raises
