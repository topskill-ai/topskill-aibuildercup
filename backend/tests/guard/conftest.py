from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.a2a.conftest import a2a_env  # noqa: F401 (fixture reused by test_chat_guard)
from tests.chat.conftest import doubles  # noqa: F401 (fixture reused by test_chat_guard)
from topskill import guard
from topskill.guard import stats
from topskill.guard.fake import FakeGuard
from topskill.guard.types import GuardVerdict, ScreenKind


@pytest.fixture
def fake_guard() -> Iterator[FakeGuard]:
    backend = FakeGuard()
    guard.set_backend(backend)
    yield backend
    guard.set_backend(None)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ScreenKind, GuardVerdict]]:
    """Capture stats.record calls instead of writing Firestore (unit tests): one entry per verdict."""
    calls: list[tuple[str, ScreenKind, GuardVerdict]] = []

    def record(agent_id, screens) -> None:
        calls.extend((agent_id, kind, v) for kind, v in screens if v is not None)

    monkeypatch.setattr(stats, "record", record)
    return calls


@pytest.fixture(autouse=True)
def _fresh_fake_llm() -> Iterator[None]:
    from topskill.testing import fake_llm

    fake_llm.reset()
    yield
    fake_llm.reset()
