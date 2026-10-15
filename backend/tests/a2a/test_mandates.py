"""Mandate signing and verification through real PyJWT decoding (SPEC §8.6, §12.1 a2a)."""

from __future__ import annotations

import base64
from datetime import timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from tests.a2a.conftest import issue_mandate
from tests.conftest import dev_headers
from tests.core.helpers import settings_env
from topskill.a2a import jwks, sign_mandate, verify_token
from topskill.a2a.mandates import TOKEN_TYPE
from topskill.db import Col, get_db, new_id, utcnow
from topskill.errors import ApiError
from topskill.testing.factories import unique

pytestmark = pytest.mark.emulator


async def _mandate_doc(
    payer_uid: str, *, audience: str = "*", status: str = "active", minutes: int = 30
) -> str:
    mandate_id = new_id("mdt")
    now = utcnow()
    await (
        get_db()
        .collection(Col.MANDATES)
        .document(mandate_id)
        .set(
            {
                "id": mandate_id,
                "payer_uid": payer_uid,
                "audience": audience,
                "intent": "Test intent",
                "payer_agent": None,
                "max_amount_cents": 500,
                "budget_cents": 2000,
                "spent_cents": 0,
                "calls": 0,
                "reserved_cents": 0,
                "status": status,
                "kid": "dev-1",
                "issued_at": now,
                "expires_at": now + timedelta(minutes=minutes),
            }
        )
    )
    return mandate_id


def _sign(mandate_id: str, payer: str, *, audience: str = "*", minutes: int = 30) -> str:
    return sign_mandate(
        mandate_id=mandate_id,
        payer_uid=payer,
        audience=audience,
        intent="Test intent",
        max_amount_cents=500,
        budget_cents=2000,
        expires_at=utcnow() + timedelta(minutes=minutes),
        payer_agent="enterprise-assistant",
    )


async def _code(token: str | None, slug: str | None) -> str:
    with pytest.raises(ApiError) as exc:
        await verify_token(token, slug)
    assert exc.value.status == 402
    return exc.value.code


async def test_sign_verify_happy_path_for_wildcard_and_slug() -> None:
    payer = unique("u_")
    mid = await _mandate_doc(payer)
    token = _sign(mid, payer)
    header = jwt.get_unverified_header(token)
    assert header == {"alg": "EdDSA", "typ": TOKEN_TYPE, "kid": "dev-1"}
    verified = await verify_token(token, "visa-advisor")
    assert verified.mandate_id == mid and verified.payer_uid == payer and verified.audience == "*"
    assert verified.max_amount_cents == 500 and verified.budget_cents == 2000
    assert verified.payer_agent == "enterprise-assistant" and verified.kid == "dev-1"
    # assistant path: any topskill:agent: audience
    assert (await verify_token(token, None)).mandate_id == mid

    mid2 = await _mandate_doc(payer, audience="visa-advisor")
    scoped = _sign(mid2, payer, audience="visa-advisor")
    assert (await verify_token(scoped, "visa-advisor")).audience == "visa-advisor"
    assert (await verify_token(scoped, None)).audience == "visa-advisor"


async def test_rejection_codes() -> None:
    payer = unique("u_")
    mid = await _mandate_doc(payer, audience="visa-advisor")
    assert await _code(None, "visa-advisor") == "mandate_missing"
    assert await _code("  ", "visa-advisor") == "mandate_missing"
    assert await _code("not-a-jwt", "visa-advisor") == "mandate_invalid"
    # wrong audience
    assert await _code(_sign(mid, payer, audience="visa-advisor"), "resume-reviewer") == "mandate_audience"
    # expired (beyond the 30 s leeway)
    assert (
        await _code(_sign(mid, payer, audience="visa-advisor", minutes=-5), "visa-advisor")
        == "mandate_expired"
    )
    assert await _code(_sign(mid, payer, audience="visa-advisor", minutes=-5), None) == "mandate_expired"
    # unknown mandate doc / payer mismatch
    assert await _code(_sign(new_id("mdt"), payer), "visa-advisor") == "mandate_invalid"
    assert await _code(_sign(mid, unique("u_"), audience="visa-advisor"), "visa-advisor") == "mandate_invalid"
    # revoked
    revoked = await _mandate_doc(payer, status="revoked")
    assert await _code(_sign(revoked, payer), "visa-advisor") == "mandate_revoked"


async def test_unknown_kid_bad_issuer_bad_signature_and_missing_claims() -> None:
    payer = unique("u_")
    mid = await _mandate_doc(payer)
    good = _sign(mid, payer)
    claims = jwt.decode(good, options={"verify_signature": False})
    other_key = Ed25519PrivateKey.generate()
    # unknown kid
    forged_kid = jwt.encode(claims, other_key, algorithm="EdDSA", headers={"kid": "other", "typ": TOKEN_TYPE})
    assert await _code(forged_kid, "visa-advisor") == "mandate_invalid"
    # right kid, wrong key → bad signature
    forged_sig = jwt.encode(claims, other_key, algorithm="EdDSA", headers={"kid": "dev-1", "typ": TOKEN_TYPE})
    assert await _code(forged_sig, "visa-advisor") == "mandate_invalid"
    # wrong algorithm in header
    hs = jwt.encode(claims, "secret-secret-secret-secret-1234", algorithm="HS256", headers={"kid": "dev-1"})
    assert await _code(hs, "visa-advisor") == "mandate_invalid"
    # bad issuer (signed with our key)
    with settings_env(pytest.MonkeyPatch(), public_api_base="https://evil.example"):
        bad_iss = _sign(mid, payer)
    assert await _code(bad_iss, "visa-advisor") == "mandate_invalid"
    assert await _code(bad_iss, None) == "mandate_invalid"
    # missing custom claim / registered claim
    from topskill.a2a.mandates import _key_set

    key = _key_set().private_key
    no_budget = {k: v for k, v in claims.items() if k != "budget"}
    token = jwt.encode(no_budget, key, algorithm="EdDSA", headers={"kid": "dev-1", "typ": TOKEN_TYPE})
    assert await _code(token, "visa-advisor") == "mandate_invalid"
    no_exp = {k: v for k, v in claims.items() if k != "exp"}
    token = jwt.encode(no_exp, key, algorithm="EdDSA", headers={"kid": "dev-1", "typ": TOKEN_TYPE})
    assert await _code(token, "visa-advisor") == "mandate_invalid"
    # aud outside our namespace on the assistant path
    foreign = {**claims, "aud": "someone-else"}
    token = jwt.encode(foreign, key, algorithm="EdDSA", headers={"kid": "dev-1", "typ": TOKEN_TYPE})
    assert await _code(token, None) == "mandate_invalid"
    assert await _code(token, "visa-advisor") == "mandate_audience"


async def test_jwks_verifies_tokens_offline(client) -> None:
    resp = await client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    body = resp.json()
    assert body == jwks()
    [key] = body["keys"]
    assert key["kty"] == "OKP" and key["crv"] == "Ed25519" and key["alg"] == "EdDSA" and key["use"] == "sig"
    assert key["kid"] == "dev-1"
    raw = base64.urlsafe_b64decode(key["x"] + "=" * (-len(key["x"]) % 4))
    public_key = Ed25519PublicKey.from_public_bytes(raw)
    payer = unique("u_")
    token = _sign(await _mandate_doc(payer), payer)
    claims = jwt.decode(token, public_key, algorithms=["EdDSA"], audience="topskill:agent:*")
    assert claims["sub"] == payer and claims["max_amount"] == {"value_cents": 500, "currency": "TSC"}


async def test_create_list_revoke_and_calls_endpoints(client) -> None:
    payer = unique("u_")
    mandate, token = await issue_mandate(client, payer, audience="visa-advisor", max_amount_cents=300)
    assert mandate["status"] == "active" and mandate["audience"] == "visa-advisor"
    assert mandate["spent_cents"] == 0 and mandate["calls"] == 0 and mandate["max_amount_cents"] == 300
    assert mandate["issued_at"].endswith("Z") and mandate["expires_at"].endswith("Z")
    verified = await verify_token(token, "visa-advisor")
    assert verified.mandate_id == mandate["id"]

    listed = (await client.get("/api/mandates", headers=dev_headers(payer))).json()
    assert [m["id"] for m in listed["items"]] == [mandate["id"]] and listed["next_cursor"] is None

    other = unique("u_")
    resp = await client.post(f"/api/mandates/{mandate['id']}/revoke", headers=dev_headers(other))
    assert resp.status_code == 403 and resp.json()["code"] == "forbidden"
    resp = await client.get(f"/api/mandates/{mandate['id']}/calls", headers=dev_headers(other))
    assert resp.status_code == 403
    resp = await client.post("/api/mandates/mdt_missing/revoke", headers=dev_headers(payer))
    assert resp.status_code == 404 and resp.json()["code"] == "not_found"

    calls = (await client.get(f"/api/mandates/{mandate['id']}/calls", headers=dev_headers(payer))).json()
    assert calls == {"items": []}

    revoked = (await client.post(f"/api/mandates/{mandate['id']}/revoke", headers=dev_headers(payer))).json()
    assert revoked["status"] == "revoked"
    assert await _code(token, "visa-advisor") == "mandate_revoked"


@pytest.mark.parametrize(
    "body",
    [
        {"audience": "*", "intent": "x", "max_amount_cents": 500, "budget_cents": 400, "ttl_minutes": 30},
        {"audience": "*", "intent": "x", "max_amount_cents": 500, "budget_cents": 100_001, "ttl_minutes": 30},
        {"audience": "*", "intent": "x", "max_amount_cents": 500, "budget_cents": 2000, "ttl_minutes": 1441},
        {"audience": "*", "intent": "x", "max_amount_cents": 0, "budget_cents": 2000, "ttl_minutes": 30},
        {
            "audience": "Bad Slug!",
            "intent": "x",
            "max_amount_cents": 500,
            "budget_cents": 2000,
            "ttl_minutes": 30,
        },
        {"audience": "*", "intent": "   ", "max_amount_cents": 500, "budget_cents": 2000, "ttl_minutes": 30},
        {
            "audience": "*",
            "intent": "x" * 201,
            "max_amount_cents": 500,
            "budget_cents": 2000,
            "ttl_minutes": 30,
        },
    ],
)
async def test_create_mandate_validation(client, body: dict) -> None:
    resp = await client.post("/api/mandates", json=body, headers=dev_headers(unique("u_")))
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "validation_error"


async def test_create_mandate_requires_auth(client) -> None:
    resp = await client.post(
        "/api/mandates",
        json={
            "audience": "*",
            "intent": "x",
            "max_amount_cents": 500,
            "budget_cents": 2000,
            "ttl_minutes": 30,
        },
    )
    assert resp.status_code == 401
