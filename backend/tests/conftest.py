"""Shared test setup (SPEC §12.1).

Environment is set BEFORE ``topskill`` is imported: code defaults are prod, so tests opt into dev values
explicitly. Emulator hosts that are already set (e.g. by ``firebase emulators:exec`` on private ports)
are honoured. Emulator tests (``@pytest.mark.emulator``) are skipped when the Firestore emulator is
unreachable; live tests (``@pytest.mark.live``) run only when selected with ``-m live``.
"""

from __future__ import annotations

import os
import socket
import tempfile
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="topskill-tests-"))

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
os.environ.setdefault("FIREBASE_AUTH_EMULATOR_HOST", "127.0.0.1:9099")
os.environ.update(
    {
        "ENV": "test",
        "AUTH_DEV_BYPASS": "true",
        "LLM_MODE": "fake",
        "KNOWLEDGE_BACKEND": "local",
        "STORAGE_BACKEND": "local",
        "LOCAL_STORAGE_DIR": str(_TMP / "blobs"),
        "MANDATE_KEY_FILE": str(_TMP / "mandate_ed25519.pem"),
        "MANDATE_KID": "dev-1",
        "FIRESTORE_PROJECT": "demo-topskill",
        "GOOGLE_CLOUD_PROJECT": "top-skill",
        "GOOGLE_CLOUD_LOCATION": "global",
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "PUBLIC_API_BASE": "http://127.0.0.1:8000",
        "PUBLIC_WEB_BASE": "http://localhost:5173",
        "CORS_ORIGINS": "http://localhost:5173,http://127.0.0.1:5173",
        "CORS_ORIGIN_REGEX": r"^https://top-skill--[a-z0-9-]+\.web\.app$",
        "APP_VERSION": "test",
        "LOG_LEVEL": "INFO",
        "TRACING_ENABLED": "false",
    }
)
os.environ.pop("K_SERVICE", None)

import httpx  # noqa: E402

from topskill.config import get_settings  # noqa: E402

get_settings.cache_clear()


def _reachable(hostport: str | None) -> bool:
    if not hostport:
        return False
    host, _, port = hostport.rpartition(":")
    try:
        with socket.create_connection((host or "127.0.0.1", int(port)), timeout=0.5):
            return True
    except OSError:
        return False


EMULATOR_UP = _reachable(os.environ.get("FIRESTORE_EMULATOR_HOST"))
AUTH_EMULATOR_UP = _reachable(os.environ.get("FIREBASE_AUTH_EMULATOR_HOST"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    markexpr = config.getoption("-m") or ""
    live_selected = "live" in markexpr and "not live" not in markexpr
    skip_emulator = pytest.mark.skip(reason="Firestore emulator not reachable")
    skip_live = pytest.mark.skip(reason="live test: run with -m live")
    for item in items:
        if "emulator" in item.keywords and not EMULATOR_UP:
            item.add_marker(skip_emulator)
        if "live" in item.keywords and not live_selected:
            item.add_marker(skip_live)


@pytest.fixture(scope="session")
def app():
    from topskill.main import create_app

    return create_app()


@pytest.fixture(scope="session")
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def dev_headers(uid: str, role: str | None = None, email: str | None = None) -> dict[str, str]:
    """Headers for the dev auth bypass (SPEC §4.2)."""
    headers = {"X-Dev-User": uid}
    if role:
        headers["X-Dev-Role"] = role
    if email:
        headers["X-Dev-Email"] = email
    return headers


@pytest.fixture
def dev_auth() -> Callable[..., dict[str, str]]:
    return dev_headers


@pytest.fixture
def uid() -> str:
    from topskill.testing.factories import unique

    return unique("u_")


async def emulator_id_token(email: str, password: str = "secret123") -> tuple[str, str]:
    """Sign up a user on the Auth emulator → ``(uid, id_token)``."""
    host = os.environ["FIREBASE_AUTH_EMULATOR_HOST"]
    url = f"http://{host}/identitytoolkit.googleapis.com/v1/accounts:signUp?key=fake-api-key"
    async with httpx.AsyncClient(trust_env=False, timeout=10) as c:
        resp = await c.post(url, json={"email": email, "password": password, "returnSecureToken": True})
        resp.raise_for_status()
        data = resp.json()
    return data["localId"], data["idToken"]
