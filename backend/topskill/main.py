"""FastAPI application (SPEC §3.2). ``uvicorn topskill.main:app``."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from topskill import tasks
from topskill.auth import init_firebase
from topskill.config import assert_safe_runtime, get_settings
from topskill.db import get_db
from topskill.errors import (
    BodySizeLimitMiddleware,
    TraceIdMiddleware,
    configure_logging,
    install_error_handlers,
)

log = logging.getLogger(__name__)

# (module, attribute, prefix) – fixed import names (SPEC §3.2). Order matters only for path precedence.
ROUTERS: tuple[tuple[str, str, str], ...] = (
    ("topskill.users.router", "router", "/api"),
    ("topskill.meta_router", "router", "/api/public"),
    ("topskill.skills.router", "router", "/api"),
    ("topskill.agents.router", "router", "/api"),
    ("topskill.agents.public_router", "router", "/api/public"),
    ("topskill.billing.router", "router", "/api"),
    ("topskill.billing.router", "public_router", "/api/public"),
    ("topskill.knowledge.router", "router", "/api"),
    ("topskill.records.router", "router", "/api"),
    ("topskill.chat.router", "public_router", "/api/public"),
    ("topskill.chat.router", "router", "/api"),
    ("topskill.a2a.mandate_router", "router", ""),
    ("topskill.a2a.router", "router", ""),
)
CORE_ROUTER_MODULES = frozenset({"topskill.users.router", "topskill.meta_router"})


def _load_router(module_name: str, attr: str) -> APIRouter | None:
    """Guarded import while lanes land (SPEC §3.1): a missing domain package logs a warning."""
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        if module_name in CORE_ROUTER_MODULES:
            raise
        missing = getattr(exc, "name", None) or ""
        if missing and module_name.startswith(missing):
            log.warning("router_not_available module=%s (package not built yet)", module_name)
        else:
            log.warning("router_import_failed module=%s error=%r", module_name, exc)
        return None
    router = getattr(module, attr, None)
    if router is None:
        log.warning("router_attribute_missing module=%s attr=%s", module_name, attr)
    return router


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

    mounted: list[str] = []
    unavailable: set[str] = set()
    for module_name, attr, prefix in ROUTERS:
        if module_name in unavailable:
            continue
        router = _load_router(module_name, attr)
        if router is None and module_name not in sys.modules:
            unavailable.add(module_name)
        if router is not None:
            app.include_router(router, prefix=prefix)
            mounted.append(f"{module_name}:{attr}")
    app.state.mounted_routers = mounted
    return app


app = create_app()
