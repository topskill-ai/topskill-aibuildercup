"""The single injection point for billing, knowledge and records (SPEC §3.1, §14.3).

``chat`` and ``a2a`` reach those packages only through :func:`get_deps`. Since the 2026-10-17
integration every package is real; the stub fallback is gone. Tests replace the resolved packages with
doubles through :func:`set_deps`.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from types import ModuleType
from typing import Any

from topskill import billing, knowledge, records


@dataclass(frozen=True)
class Deps:
    billing: ModuleType | Any
    knowledge: ModuleType | Any
    records: ModuleType | Any


def is_stub(dep: Any) -> bool:
    """True when ``dep`` is a stub or test double that asks callers to skip optional work."""
    return bool(getattr(dep, "is_stub", False))


_override: Deps | None = None


@lru_cache(maxsize=1)
def _resolved() -> Deps:
    return Deps(billing=billing, knowledge=knowledge, records=records)


def real_deps() -> Deps:
    """The real packages, ignoring any test override."""
    return _resolved()


def get_deps() -> Deps:
    """The real billing, knowledge and records packages (or the test override); cached."""
    return _override if _override is not None else _resolved()


def set_deps(deps: Deps | None) -> None:
    """Tests only: replace the resolved dependencies (``None`` restores the real packages)."""
    global _override
    _override = deps
