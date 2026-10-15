"""FastAPI application (SPEC §3.2). ``uvicorn topskill.main:app``."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from topskill import tasks
from topskill.a2a.mandate_router import router as mandate_router
from topskill.a2a.router import router as a2a_router
from topskill.agents.public_router import router as agents_public_router
from topskill.agents.router import router as agents_router
from topskill.auth import init_firebase
from topskill.billing.router import public_router as billing_public_router
from topskill.billing.router import router as billing_router
from topskill.chat.router import public_router as chat_public_router
from topskill.chat.router import router as chat_router
from topskill.config import assert_safe_runtime, get_settings
from topskill.db import get_db
from topskill.errors import (
    BodySizeLimitMiddleware,
    TraceIdMiddleware,
    configure_logging,
    install_error_handlers,
)
from topskill.guard import warm_up as guard_warm_up
from topskill.knowledge import sweep_stale_all
from topskill.knowledge.router import router as knowledge_router
from topskill.meta_router import router as meta_router
from topskill.records.router import router as records_router
from topskill.skills.router import router as skills_router
from topskill.users.router import router as users_router
from topskill.voice.router import router as voice_router

log = logging.getLogger(__name__)

# (router, prefix, name) – fixed import names (SPEC §3.2). Order matters only for path precedence.
ROUTERS: tuple[tuple[APIRouter, str, str], ...] = (
    (users_router, "/api", "topskill.users.router:router"),
    (meta_router, "/api/public", "topskill.meta_router:router"),
    (skills_router, "/api", "topskill.skills.router:router"),
    (agents_router, "/api", "topskill.agents.router:router"),
    (agents_public_router, "/api/public", "topskill.agents.public_router:router"),
    (billing_router, "/api", "topskill.billing.router:router"),
    (billing_public_router, "/api/public", "topskill.billing.router:public_router"),
    (knowledge_router, "/api", "topskill.knowledge.router:router"),
    (records_router, "/api", "topskill.records.router:router"),
    (chat_public_router, "/api/public", "topskill.chat.router:public_router"),
    (chat_router, "/api", "topskill.chat.router:router"),
    (mandate_router, "", "topskill.a2a.mandate_router:router"),
    (a2a_router, "", "topskill.a2a.router:router"),
    (voice_router, "/api/public", "topskill.voice.router:router"),
)


def mounted_routers(app: FastAPI) -> list[str]:
    return list(getattr(app.state, "mounted_routers", []))


async def _warm_import_adk() -> None:
    await asyncio.to_thread(importlib.import_module, "google.adk")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    assert_safe_runtime(settings, os.environ)  # RuntimeError → the container exits
    get_db()
    init_firebase()
    tasks.spawn(_warm_import_adk(), name="warm-import:google.adk")
    tasks.spawn(sweep_stale_all(), name="knowledge:startup-sweep")  # never raises (SPEC §10.1)
    tasks.spawn(guard_warm_up(), name="guard:warm-up")  # P2 guard (SPEC §18 "Guard"); never raises
    log.info("startup env=%s version=%s routers=%s", settings.env, settings.app_version, mounted_routers(app))
    try:
        yield
    finally:
        await tasks.drain(timeout=8)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(
        level=settings.log_level, json_logs=settings.is_prod, project=settings.google_cloud_project
    )
    app = FastAPI(
        title="TopSkill API",
        version=settings.app_version,
        generate_unique_id_function=lambda r: r.name,  # operationId = view function name
        lifespan=lifespan,
    )
    install_error_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(TraceIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex or None,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Dev-User",
            "X-Dev-Role",
            "X-Dev-Email",
            "X-AP2-Mandate",
            "A2A-Version",
            "X-Request-Id",
        ],
        expose_headers=["X-Trace-Id", "Content-Disposition"],
        allow_credentials=False,
        max_age=3600,
    )

    async def healthz() -> dict:
        return {"ok": True, "env": settings.env, "version": settings.app_version}

    app.get("/healthz", tags=["health"])(healthz)
    # Cloud Run's front end answers paths ending in "z" itself, so /healthz is only reachable from inside
    # the container (startup probe). /health is the same handler for external checks.
    app.get("/health", tags=["health"], include_in_schema=False)(healthz)

    mounted: list[str] = []
    for router, prefix, name in ROUTERS:
        app.include_router(router, prefix=prefix)
        mounted.append(name)
    app.state.mounted_routers = mounted
    return app


app = create_app()
