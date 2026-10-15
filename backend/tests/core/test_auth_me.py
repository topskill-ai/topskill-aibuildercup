"""Auth (Bearer via the Auth emulator, dev bypass) and /api/me* on the emulators."""

from __future__ import annotations

import httpx
import pytest
from fastapi import Depends, FastAPI

from tests.conftest import AUTH_EMULATOR_UP, dev_headers, emulator_id_token
from tests.core.helpers import settings_env
from topskill.auth import CurrentUser, get_optional_user, require_creator
from topskill.main import create_app
from topskill.repo import get_user
from topskill.testing.factories import unique

pytestmark = pytest.mark.emulator


def build_app() -> FastAPI:
    app = create_app()

    @app.get("/test/creator-only")
    async def creator_only(user: CurrentUser = Depends(require_creator)) -> dict:
        return {"uid": user.uid}

    @app.get("/test/optional")
    async def optional(user: CurrentUser | None = Depends(get_optional_user)) -> dict:
        return {"uid": user.uid if user else None}

    return app


@pytest.fixture(scope="module")
async def c():
    transport = httpx.ASGITransport(app=build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_no_credentials_is_401(c: httpx.AsyncClient) -> None:
    resp = await c.get("/api/me")
    assert resp.status_code == 401 and resp.json()["code"] == "unauthenticated"
    assert (await c.get("/test/optional")).json() == {"uid": None}


async def test_dev_bypass_creates_visitor_profile(c: httpx.AsyncClient) -> None:
    uid = unique("dev_")
    resp = await c.get("/api/me", headers=dev_headers(uid))
    assert resp.status_code == 200, resp.text
    me = resp.json()
    assert me == {
        "uid": uid,
        "email": f"{uid}@dev.topskill.local",
        "display_name": uid,
        "photo_url": None,
        "role": "visitor",
        "creator_profile": None,
        "created_at": me["created_at"],
    }
    assert me["created_at"].endswith("Z")
    stored = await get_user(uid)
    assert stored is not None and stored.role == "visitor"


async def test_dev_role_header_creates_and_updates_role(c: httpx.AsyncClient) -> None:
    uid = unique("dev_")
    creator = (await c.get("/api/me", headers=dev_headers(uid, "creator", "ana@example.com"))).json()
    assert creator["role"] == "creator" and creator["email"] == "ana@example.com"
    assert creator["display_name"] == "ana"
    assert creator["creator_profile"] == {"headline": "", "bio": ""}
    # no role header → stored role kept
    assert (await c.get("/api/me", headers=dev_headers(uid))).json()["role"] == "creator"
    # differing role header → updated (dev convenience)
    assert (await c.get("/api/me", headers=dev_headers(uid, "visitor"))).json()["role"] == "visitor"


async def test_dev_bypass_rejects_bad_headers(c: httpx.AsyncClient) -> None:
    assert (await c.get("/api/me", headers={"X-Dev-User": "a b"})).status_code == 401
    assert (await c.get("/api/me", headers=dev_headers(unique("dev_"), "admin"))).status_code == 401


async def test_dev_bypass_ignored_in_prod(c: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    with settings_env(monkeypatch, env="prod"):
        resp = await c.get("/api/me", headers=dev_headers(unique("dev_"), "creator"))
    assert resp.status_code == 401 and resp.json()["code"] == "unauthenticated"


async def test_dev_bypass_off_when_flag_false(c: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    with settings_env(monkeypatch, auth_dev_bypass="false"):
        resp = await c.get("/api/me", headers=dev_headers(unique("dev_")))
    assert resp.status_code == 401


async def test_require_creator(c: httpx.AsyncClient) -> None:
    uid = unique("dev_")
    resp = await c.get("/test/creator-only", headers=dev_headers(uid, "visitor"))
    assert resp.status_code == 403 and resp.json()["code"] == "creator_required"
    resp = await c.get("/test/creator-only", headers=dev_headers(uid, "creator"))
    assert resp.status_code == 200 and resp.json() == {"uid": uid}


@pytest.mark.skipif(not AUTH_EMULATOR_UP, reason="Auth emulator not reachable")
async def test_bearer_token_from_auth_emulator(c: httpx.AsyncClient) -> None:
    email = f"{unique('bearer')}@example.com"
    uid, token = await emulator_id_token(email)
    resp = await c.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    me = resp.json()
    assert me["uid"] == uid and me["email"] == email and me["role"] == "visitor"
    assert me["display_name"] == email.split("@")[0]


@pytest.mark.skipif(not AUTH_EMULATOR_UP, reason="Auth emulator not reachable")
async def test_bearer_wins_over_dev_headers(c: httpx.AsyncClient) -> None:
    uid, token = await emulator_id_token(f"{unique('bearer')}@example.com")
    headers = {"Authorization": f"Bearer {token}", **dev_headers(unique("dev_"), "creator")}
    me = (await c.get("/api/me", headers=headers)).json()
    assert me["uid"] == uid and me["role"] == "visitor"


async def test_invalid_bearer_is_401(c: httpx.AsyncClient) -> None:
    for header in ("Bearer not-a-token", "Basic abc", "Bearer "):
        resp = await c.get("/api/me", headers={"Authorization": header, **dev_headers(unique("dev_"))})
        assert resp.status_code == 401, header
        assert resp.json()["code"] == "unauthenticated"
    resp = await c.get("/test/optional", headers={"Authorization": "Bearer junk"})
    assert resp.status_code == 401


async def test_patch_me(c: httpx.AsyncClient) -> None:
    uid = unique("dev_")
    headers = dev_headers(uid)
    resp = await c.patch("/api/me", json={"display_name": "  Ana Silva  "}, headers=headers)
    assert resp.status_code == 200 and resp.json()["display_name"] == "Ana Silva"
    resp = await c.patch(
        "/api/me",
        json={"creator_profile": {"headline": "Immigration consultant", "bio": "10 years."}},
        headers=headers,
    )
    assert resp.json()["creator_profile"] == {"headline": "Immigration consultant", "bio": "10 years."}
    assert (await c.get("/api/me", headers=headers)).json()["display_name"] == "Ana Silva"
    for bad in (
        {"display_name": ""},
        {"display_name": "   "},
        {"display_name": "x" * 61},
        {"creator_profile": {"headline": "x" * 81}},
        {"role": "creator"},
    ):
        resp = await c.patch("/api/me", json=bad, headers=headers)
        assert resp.status_code == 400 and resp.json()["code"] == "validation_error", bad


async def test_become_creator_idempotent(c: httpx.AsyncClient) -> None:
    uid = unique("dev_")
    headers = dev_headers(uid)
    resp = await c.post("/api/me/become-creator", json={"headline": "Recruiter"}, headers=headers)
    assert resp.status_code == 200
    me = resp.json()
    assert me["role"] == "creator" and me["creator_profile"] == {"headline": "Recruiter", "bio": ""}
    again = (await c.post("/api/me/become-creator", headers=headers)).json()
    assert again["role"] == "creator" and again["creator_profile"]["headline"] == "Recruiter"
    stored = await get_user(uid)
    assert stored is not None and stored.role == "creator"
    resp = await c.get("/test/creator-only", headers=headers)
    assert resp.status_code == 200
