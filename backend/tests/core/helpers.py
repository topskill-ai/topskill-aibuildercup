"""Small helpers shared by core tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from topskill.config import get_settings


@contextmanager
def settings_env(monkeypatch: pytest.MonkeyPatch, **values: str | None) -> Iterator[None]:
    """Temporarily override settings via env vars (``None`` deletes the var)."""
    with monkeypatch.context() as m:
        for key, value in values.items():
            if value is None:
                m.delenv(key.upper(), raising=False)
            else:
                m.setenv(key.upper(), value)
        get_settings.cache_clear()
        try:
            yield
        finally:
            get_settings.cache_clear()
    get_settings.cache_clear()
