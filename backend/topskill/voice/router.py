"""Voice endpoints (SPEC §18.1), mounted under ``/api/public``.

* ``GET /api/public/voice/config`` — whether voice mode is on, and its limits (the web shows the mic
  button only when ``enabled``; any failure of this call simply hides it).
* ``WS /api/public/agents/{slug}/voice`` — one live voice session (protocol in ``voice.protocol``).

Firebase Hosting does not proxy WebSockets, so browsers connect to the Cloud Run URL directly (like SSE).
The ID token travels in the first frame, never in the URL. Browser origins are checked against the CORS
allow-list (a WebSocket is not subject to CORS); clients without an ``Origin`` header (scripts) pass.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re

from fastapi import APIRouter, WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect, WebSocketState

from topskill import ratelimit
from topskill.auth import authenticate_credentials
from topskill.chat import locks
from topskill.config import get_settings
from topskill.errors import ApiError, Codes, current_trace_id
from topskill.voice import registry
from topskill.voice.protocol import (
    INPUT_SAMPLE_RATE,
    OUTPUT_SAMPLE_RATE,
    ClosedEvent,
    ErrorEvent,
    StartMessage,
    close_code_for,
)
from topskill.voice.session import VoiceSession, prepare

log = logging.getLogger(__name__)

router = APIRouter(tags=["voice"])

RATE_BUCKET = "voice_session"


@router.get("/voice/config")
async def voice_config() -> dict:
    settings = get_settings()
    return {
        "enabled": settings.voice_enabled,
        "max_session_s": settings.voice_max_session_s,
        "idle_timeout_s": settings.voice_idle_timeout_s,
        "input_sample_rate": INPUT_SAMPLE_RATE,
        "output_sample_rate": OUTPUT_SAMPLE_RATE,
    }


def origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    settings = get_settings()
    if origin in settings.cors_origins:
        return True
    return bool(settings.cors_origin_regex) and re.fullmatch(settings.cors_origin_regex, origin) is not None


async def read_start(ws: WebSocket) -> StartMessage:
    """The first frame must be a JSON ``start`` message, within ``VOICE_AUTH_TIMEOUT_S``."""
    timeout = get_settings().voice_auth_timeout_s
    try:
        message = await asyncio.wait_for(ws.receive(), timeout=timeout)
    except TimeoutError as exc:
        raise ApiError(401, Codes.UNAUTHENTICATED, "Please sign in to start a voice call.") from exc
    if message.get("type") == "websocket.disconnect":
        raise WebSocketDisconnect(code=message.get("code", 1000))
    text = message.get("text")
    try:
        return StartMessage.model_validate(json.loads(text or ""))
    except (ValueError, ValidationError) as exc:
        raise ApiError(400, Codes.VALIDATION_ERROR, "The first message must be a start message.") from exc


async def _send_error_and_close(ws: WebSocket, error: ApiError) -> None:
    if ws.application_state != WebSocketState.CONNECTED or ws.client_state != WebSocketState.CONNECTED:
        return
    with contextlib.suppress(Exception):
        await ws.send_text(
            ErrorEvent(
                code=error.code, message=error.message, trace_id=current_trace_id(), detail=error.detail
            ).model_dump_json()
        )
        await ws.send_text(ClosedEvent(reason="error").model_dump_json())
        await ws.close(code=close_code_for(error.status))


@router.websocket("/agents/{slug}/voice")
async def voice_socket(ws: WebSocket, slug: str) -> None:
    settings = get_settings()
    if not origin_allowed(ws.headers.get("origin")):
        log.warning("voice_origin_rejected origin=%s", ws.headers.get("origin"))
        await ws.close(code=close_code_for(403))
        return
    await ws.accept()
    session: VoiceSession | None = None
    uid: str | None = None
    lock_held: str | None = None
    try:
        if not settings.voice_enabled:
            raise ApiError(
                503, Codes.SERVICE_UNAVAILABLE, "Voice mode is turned off. You can keep chatting by text."
            )
        start = await read_start(ws)
        dev = start.dev
        user = await authenticate_credentials(
            token=start.token,
            dev_uid=dev.uid if dev else None,
            dev_role=dev.role if dev else None,
            dev_email=dev.email if dev else None,
        )
        uid = user.uid
        await ratelimit.hit(RATE_BUCKET, user.uid, limit=settings.voice_sessions_per_hour, window="hour")
        setup = await prepare(slug, user, start.conversation_id)
        session = VoiceSession(ws, setup)
        await registry.claim(user.uid, session)
        # A text reply running in this conversation → 409 turn_in_progress; the lock also keeps text
        # turns out of the conversation while the call lasts and counts toward MAX_CONCURRENT_TURNS.
        locks.acquire_turn(setup.conversation_id)
        lock_held = setup.conversation_id
        await session.run()
    except ApiError as exc:
        if exc.status >= 500:
            log.error("voice_error status=%s code=%s message=%s", exc.status, exc.code, exc.message)
        await _send_error_and_close(ws, exc)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("voice_internal_error slug=%s", slug)
        await _send_error_and_close(
            ws, ApiError(500, Codes.INTERNAL_ERROR, "Something went wrong. Please try again.")
        )
    finally:
        if lock_held is not None:
            locks.release_turn(lock_held)
        if session is not None:
            if uid is not None:
                registry.release(uid, session)
            session.finished.set()
