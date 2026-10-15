"""AP2-style intent mandates: Ed25519 JWS signing, verification and call reservation (SPEC §8.6).

The platform signs a spend envelope that the signed-in payer authorised (audience, intent, per-call cap,
total budget, expiry). Any party can verify a token offline with ``/.well-known/jwks.json``. Server-side
verification adds the Firestore checks (mandate exists, belongs to ``sub``, not revoked); budget is
reserved per A2A message in transaction T8 so that a message can be paid only once and parallel calls
never overspend.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from google.cloud import firestore

from topskill.config import get_settings
from topskill.db import Col, get_db, utcnow
from topskill.domain import AgentDoc, VerifiedMandate
from topskill.errors import ApiError, Codes

log = logging.getLogger(__name__)

TOKEN_TYPE = "ap2-intent-mandate+jwt"
CURRENCY = "TSC"  # TopSkill credits
AUDIENCE_PREFIX = "topskill:agent:"
LEEWAY_S = 30
REQUIRED_CLAIMS = ["exp", "iat", "nbf", "iss", "sub", "jti", "aud"]
# Contention on one mandate doc (parallel calls) retries more often than the default 5 attempts.
RESERVE_MAX_ATTEMPTS = 15

_MESSAGES: dict[str, str] = {
    Codes.MANDATE_MISSING: "A payment mandate is required to call this agent.",
    Codes.MANDATE_INVALID: "The payment mandate is invalid.",
    Codes.MANDATE_EXPIRED: "The payment mandate has expired.",
    Codes.MANDATE_REVOKED: "The payment mandate was revoked.",
    Codes.MANDATE_AUDIENCE: "The payment mandate is not valid for this agent.",
    Codes.MANDATE_EXCEEDS_MAX: "This call costs more than the mandate allows per call.",
    Codes.MANDATE_BUDGET_EXHAUSTED: "The mandate budget is used up.",
    Codes.MANDATE_REPLAY: "This message was already paid for.",
}


def mandate_error(code: str, detail: dict | None = None) -> ApiError:
    """``ApiError(402, <mandate_* code>)`` with a user-safe message."""
    return ApiError(402, code, _MESSAGES.get(code, "The payment mandate was rejected."), detail or {})


# --------------------------------------------------------------------------- keys


@dataclass(frozen=True)
class _KeySet:
    kid: str
    private_key: Ed25519PrivateKey
    public_keys: dict[str, Ed25519PublicKey]


_keys_lock = threading.Lock()
_keys_cache: dict[tuple[str, str, str], _KeySet] = {}


def _load_pem(pem: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise RuntimeError("the mandate signing key is not an Ed25519 key")
    return key


def _load_or_create_file(path: Path) -> Ed25519PrivateKey:
    """Dev/test only: read the PEM at ``path`` or create it (0600) when missing."""
    if path.exists():
        return _load_pem(path.read_bytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # another process created it first
        return _load_pem(path.read_bytes())
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    log.info("mandate_dev_key_created path=%s", path)
    return key


def _key_set() -> _KeySet:
    settings = get_settings()
    pem = settings.mandate_signing_key.get_secret_value().strip() if settings.mandate_signing_key else ""
    path = settings.mandate_key_path
    cache_key = (settings.mandate_kid, hashlib.sha256(pem.encode()).hexdigest() if pem else "", str(path))
    with _keys_lock:
        cached = _keys_cache.get(cache_key)
        if cached is not None:
            return cached
        if pem:
            private_key = _load_pem(pem.encode())
        elif path is not None and not settings.is_prod:
            private_key = _load_or_create_file(path)
        else:
            log.error("mandate_key_missing env=%s", settings.env)
            raise ApiError(503, Codes.SERVICE_UNAVAILABLE, "Payments are temporarily unavailable.")
        key_set = _KeySet(
            kid=settings.mandate_kid,
            private_key=private_key,
            public_keys={settings.mandate_kid: private_key.public_key()},
        )
        _keys_cache.clear()
        _keys_cache[cache_key] = key_set
        return key_set


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def jwks() -> dict:
    """Public keys as a JWK set (``/.well-known/jwks.json``)."""
    key_set = _key_set()
    keys = []
    for kid, public_key in key_set.public_keys.items():
        raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        keys.append(
            {"kty": "OKP", "crv": "Ed25519", "x": _b64url(raw), "kid": kid, "use": "sig", "alg": "EdDSA"}
        )
    return {"keys": keys}


# --------------------------------------------------------------------------- tokens


def audience_claim(audience: str) -> str:
    """``"*"`` or a slug → the JWT ``aud`` value."""
    return f"{AUDIENCE_PREFIX}{audience}"


def sign_mandate(
    *,
    mandate_id: str,
    payer_uid: str,
    audience: str,
    intent: str,
    max_amount_cents: int,
    budget_cents: int,
    expires_at: datetime,
    payer_agent: str | None,
    issued_at: datetime | None = None,
) -> str:
    """Compact EdDSA JWS for a mandate whose Firestore doc already exists."""
    key_set = _key_set()
    iat = int((issued_at or utcnow()).timestamp())
    claims: dict[str, Any] = {
        "iss": get_settings().public_api_base,
        "sub": payer_uid,
        "jti": mandate_id,
        "aud": audience_claim(audience),
        "intent": intent,
        "max_amount": {"value_cents": max_amount_cents, "currency": CURRENCY},
        "budget": {"value_cents": budget_cents, "currency": CURRENCY},
        "payer_agent": payer_agent,
        "iat": iat,
        "nbf": iat,
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(
        claims, key_set.private_key, algorithm="EdDSA", headers={"kid": key_set.kid, "typ": TOKEN_TYPE}
    )


def _cents(claims: dict, name: str) -> int:
    value = claims.get(name)
    cents = value.get("value_cents") if isinstance(value, dict) else None
    if not isinstance(cents, int) or isinstance(cents, bool) or cents < 0:
        raise mandate_error(Codes.MANDATE_INVALID)
    return cents


def _decode(token: str, slug: str | None) -> tuple[dict, str]:
    key_set = _key_set()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise mandate_error(Codes.MANDATE_INVALID) from exc
    kid = header.get("kid")
    if header.get("alg") != "EdDSA" or not isinstance(kid, str) or kid not in key_set.public_keys:
        raise mandate_error(Codes.MANDATE_INVALID)
    issuer = get_settings().public_api_base
    try:
        if slug is None:
            claims = jwt.decode(
                token,
                key_set.public_keys[kid],
                algorithms=["EdDSA"],
                issuer=issuer,
                leeway=LEEWAY_S,
                options={"verify_aud": False, "require": REQUIRED_CLAIMS},
            )
        else:
            claims = jwt.decode(
                token,
                key_set.public_keys[kid],
                algorithms=["EdDSA"],
                issuer=issuer,
                audience=[audience_claim(slug), audience_claim("*")],
                leeway=LEEWAY_S,
                options={"require": REQUIRED_CLAIMS},
            )
    except (jwt.ExpiredSignatureError, jwt.ImmatureSignatureError) as exc:
        raise mandate_error(Codes.MANDATE_EXPIRED) from exc
    except jwt.InvalidAudienceError as exc:
        raise mandate_error(Codes.MANDATE_AUDIENCE) from exc
    except jwt.PyJWTError as exc:
        raise mandate_error(Codes.MANDATE_INVALID) from exc
    aud = claims.get("aud")
    if not isinstance(aud, str) or not aud.startswith(AUDIENCE_PREFIX) or aud == AUDIENCE_PREFIX:
        raise mandate_error(Codes.MANDATE_INVALID if slug is None else Codes.MANDATE_AUDIENCE)
    return claims, kid


async def verify_token(token: str | None, slug: str | None) -> VerifiedMandate:
    """SPEC §8.6 steps 1–3. ``slug=None`` = Enterprise Assistant path (any ``topskill:agent:`` audience).

    Raises ``ApiError(402, mandate_missing | mandate_invalid | mandate_expired | mandate_audience |
    mandate_revoked)``.
    """
    if not isinstance(token, str) or not token.strip():
        raise mandate_error(Codes.MANDATE_MISSING)
    claims, kid = _decode(token.strip(), slug)
    intent = claims.get("intent")
    payer_agent = claims.get("payer_agent")
    sub, jti = claims.get("sub"), claims.get("jti")
    if (
        not isinstance(intent, str)
        or not isinstance(sub, str)
        or not isinstance(jti, str)
        or not jti
        or "/" in jti
        or (payer_agent is not None and not isinstance(payer_agent, str))
    ):
        raise mandate_error(Codes.MANDATE_INVALID)
    max_amount_cents = _cents(claims, "max_amount")
    budget_cents = _cents(claims, "budget")

    snap = await get_db().collection(Col.MANDATES).document(jti).get()
    data = snap.to_dict() if snap.exists else None
    if not data or data.get("payer_uid") != sub:
        raise mandate_error(Codes.MANDATE_INVALID)
    if data.get("status") == "revoked":
        raise mandate_error(Codes.MANDATE_REVOKED)
    return VerifiedMandate(
        mandate_id=jti,
        payer_uid=sub,
        audience=claims["aud"][len(AUDIENCE_PREFIX) :],
        intent=intent,
        max_amount_cents=max_amount_cents,
        budget_cents=budget_cents,
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
        kid=kid,
        payer_agent=payer_agent,
    )


# --------------------------------------------------------------------------- reservation (T8)


def call_id_for(mandate_id: str, message_id: str) -> str:
    """``sha256(f"{mandate_id}:{message_id}")[:32]`` – one paid call per A2A message."""
    return hashlib.sha256(f"{mandate_id}:{message_id}".encode()).hexdigest()[:32]


async def reserve_call(
    mandate: VerifiedMandate, agent: AgentDoc, message_id: str, request_preview: str, price_cents: int
) -> str:
    """Transaction T8: replay check, budget check including running reservations, then
    ``reserved_cents += price`` and the ``running`` call doc with the price snapshot. Returns the call id.

    Raises ``ApiError(402, mandate_replay | mandate_budget_exhausted | mandate_exceeds_max |
    mandate_revoked | mandate_invalid)``.
    """
    db = get_db()
    call_id = call_id_for(mandate.mandate_id, message_id)
    call_ref = db.collection(Col.A2A_CALLS).document(call_id)
    mandate_ref = db.collection(Col.MANDATES).document(mandate.mandate_id)

    @firestore.async_transactional
    async def _reserve(tx: Any) -> None:
        call_snap = await call_ref.get(transaction=tx)
        mandate_snap = await mandate_ref.get(transaction=tx)
        if call_snap.exists:
            raise mandate_error(Codes.MANDATE_REPLAY)
        data = mandate_snap.to_dict() if mandate_snap.exists else None
        if not data or data.get("payer_uid") != mandate.payer_uid:
            raise mandate_error(Codes.MANDATE_INVALID)
        if data.get("status") == "revoked":
            raise mandate_error(Codes.MANDATE_REVOKED)
        max_amount = int(data.get("max_amount_cents", 0))
        if price_cents > max_amount:
            raise mandate_error(
                Codes.MANDATE_EXCEEDS_MAX, {"price_cents": price_cents, "max_amount_cents": max_amount}
            )
        spent = int(data.get("spent_cents", 0))
        reserved = int(data.get("reserved_cents", 0))
        budget = int(data.get("budget_cents", 0))
        if spent + reserved + price_cents > budget:
            raise mandate_error(
                Codes.MANDATE_BUDGET_EXHAUSTED,
                {"price_cents": price_cents, "remaining_cents": max(0, budget - spent - reserved)},
            )
        now = utcnow()
        tx.update(mandate_ref, {"reserved_cents": reserved + price_cents})
        tx.create(
            call_ref,
            {
                "id": call_id,
                "mandate_id": mandate.mandate_id,
                "payer_uid": mandate.payer_uid,
                "agent_id": agent.id,
                "agent_slug": agent.slug,
                "agent_title": agent.title,
                "owner_uid": agent.owner_uid,
                "message_id": message_id,
                "task_id": None,
                "status": "running",
                "price_cents": price_cents,
                "charged_cents": 0,
                "creator_share_cents": 0,
                "model": None,
                "error_code": None,
                "request_preview": request_preview[:120],
                "answer_preview": "",
                "created_at": now,
                "finished_at": None,
            },
        )

    await _reserve(db.transaction(max_attempts=RESERVE_MAX_ATTEMPTS))
    return call_id
