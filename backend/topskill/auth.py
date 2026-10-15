"""Authentication (SPEC §4.2, §6.1, §14.2).

``Authorization: Bearer <Firebase ID token>`` is verified with ``firebase_admin`` in a worker thread
(the call may fetch Google's public certificates). In dev/test with ``AUTH_DEV_BYPASS=true`` the
``X-Dev-User`` / ``X-Dev-Role`` / ``X-Dev-Email`` headers are accepted instead; a Bearer token always
wins, and in prod the headers are ignored. The role is read from Firestore on every request.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any

import firebase_admin
from fastapi import Depends, Request
from firebase_admin import auth as firebase_auth
from google.api_core import exceptions as gexc

from topskill.config import get_settings
from topskill.db import Col, get_db, utcnow
from topskill.domain import Role, UserDoc
from topskill.errors import ApiError, Codes

log = logging.getLogger(__name__)

DEV_UID_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
DEV_EMAIL_DOMAIN = "dev.topskill.local"
_ROLES: tuple[Role, ...] = ("visitor", "creator")

_firebase_lock = threading.Lock()


@dataclass(frozen=True)
class CurrentUser:
    uid: str
    email: str | None
    display_name: str
    role: Role

    @property
    def is_creator(self) -> bool:
        return self.role == "creator"


@dataclass(frozen=True)
class _Identity:
    uid: str
    email: str | None
    name: str | None
    photo_url: str | None
    dev_role: Role | None = None


def init_firebase() -> firebase_admin.App:
    """Initialise the default ``firebase_admin`` app once (``projectId = FIRESTORE_PROJECT``)."""
    with _firebase_lock:
        try:
            return firebase_admin.get_app()
        except ValueError:
            return firebase_admin.initialize_app(options={"projectId": get_settings().firestore_project})


def _unauthenticated(message: str = "Please sign in.") -> ApiError:
    return ApiError(401, Codes.UNAUTHENTICATED, message)


def _verify_id_token(token: str) -> dict[str, Any]:
    app = init_firebase()
    return firebase_auth.verify_id_token(token, app=app, clock_skew_seconds=10)


async def _identity_from_bearer(token: str) -> _Identity:
    try:
        claims = await asyncio.to_thread(_verify_id_token, token)
    except (ValueError, firebase_auth.InvalidIdTokenError, firebase_auth.ExpiredIdTokenError) as exc:
        raise _unauthenticated("Your session has expired. Please sign in again.") from exc
    except firebase_auth.CertificateFetchError as exc:
        log.error("firebase_cert_fetch_failed error=%s", exc)
        raise ApiError(503, Codes.SERVICE_UNAVAILABLE, "Sign-in is temporarily unavailable.") from exc
    except Exception as exc:  # any other verification failure is an invalid token
        log.warning("id_token_rejected error=%s", type(exc).__name__)
        raise _unauthenticated() from exc
    uid = claims.get("uid") or claims.get("sub")
    if not uid:
        raise _unauthenticated()
    return _Identity(
        uid=uid, email=claims.get("email"), name=claims.get("name"), photo_url=claims.get("picture")
    )


def _identity_from_dev_headers(request: Request) -> _Identity | None:
    uid = request.headers.get("x-dev-user")
    if uid is None:
        return None
    uid = uid.strip()
    if not DEV_UID_RE.match(uid):
        raise _unauthenticated("Invalid developer user id.")
    raw_role = (request.headers.get("x-dev-role") or "").strip().lower() or None
    if raw_role is not None and raw_role not in _ROLES:
        raise _unauthenticated("Invalid developer role.")
    email = (request.headers.get("x-dev-email") or "").strip() or f"{uid}@{DEV_EMAIL_DOMAIN}"
    return _Identity(uid=uid, email=email, name=None, photo_url=None, dev_role=raw_role)  # type: ignore[arg-type]


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthenticated()
    return token.strip()


def default_display_name(name: str | None, email: str | None, uid: str) -> str:
    if name and name.strip():
        return name.strip()[:60]
    if email and "@" in email:
        local = email.split("@", 1)[0].strip()
        if local:
            return local[:60]
    return uid[:60]


async def ensure_user(
    uid: str,
    *,
    email: str | None,
    name: str | None = None,
    photo_url: str | None = None,
    role: Role | None = None,
    update_role: bool = False,
) -> UserDoc:
    """Create ``users/{uid}`` on first sight (idempotent ``create()``), else read it.

    ``role`` is applied on creation; with ``update_role`` (dev bypass) a differing stored role is updated.
    """
    ref = get_db().collection(Col.USERS).document(uid)
    snap = await ref.get()
    if not snap.exists:
        now = utcnow()
        doc = UserDoc(
            uid=uid,
            email=email,
            display_name=default_display_name(name, email, uid),
            photo_url=photo_url,
            role=role or "visitor",
            creator_profile={"headline": "", "bio": ""} if role == "creator" else None,
            created_at=now,
            updated_at=now,
        )
        try:
            await ref.create(doc.model_dump(mode="python"))
            return doc
        except (gexc.AlreadyExists, gexc.Conflict):
            snap = await ref.get()
    user = UserDoc.model_validate(snap.to_dict())
    if update_role and role is not None and user.role != role:
        updates: dict[str, Any] = {"role": role, "updated_at": utcnow()}
        if role == "creator" and user.creator_profile is None:
            updates["creator_profile"] = {"headline": "", "bio": ""}
        await ref.update(updates)
        user = user.model_copy(update=updates)
    return user


def to_current_user(user: UserDoc) -> CurrentUser:
    return CurrentUser(uid=user.uid, email=user.email, display_name=user.display_name, role=user.role)


async def _resolve(request: Request) -> CurrentUser | None:
    cached = getattr(request.state, "current_user", None)
    if cached is not None:
        return cached
    settings = get_settings()
    identity: _Identity | None = None
    token = _bearer_token(request)
    if token is not None:
        identity = await _identity_from_bearer(token)
    elif settings.dev_bypass_active:
        identity = _identity_from_dev_headers(request)
    if identity is None:
        return None
    try:
        user_doc = await ensure_user(
            identity.uid,
            email=identity.email,
            name=identity.name,
            photo_url=identity.photo_url,
            role=identity.dev_role,
            update_role=identity.dev_role is not None,
        )
    except gexc.GoogleAPICallError as exc:
        log.error("user_profile_unavailable error=%s", exc)
        raise ApiError(503, Codes.SERVICE_UNAVAILABLE, "The service is temporarily unavailable.") from exc
    user = to_current_user(user_doc)
    request.state.user_doc = user_doc
    request.state.current_user = user
    return user


async def get_current_user(request: Request) -> CurrentUser:
    """FastAPI dependency: the signed-in user, else 401 ``unauthenticated``."""
    user = await _resolve(request)
    if user is None:
        raise _unauthenticated()
    return user


async def get_optional_user(request: Request) -> CurrentUser | None:
    """The signed-in user or ``None`` when no credentials were sent (an invalid token is still 401)."""
    return await _resolve(request)


async def require_creator(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """403 ``creator_required`` for visitors."""
    if not user.is_creator:
        raise ApiError(403, Codes.CREATOR_REQUIRED, "This requires a creator account.")
    return user
