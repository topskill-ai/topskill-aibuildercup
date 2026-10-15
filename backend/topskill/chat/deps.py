"""The single injection point for billing, knowledge and records (SPEC §3.1, §14.3).

``chat`` and ``a2a`` reach those packages only through :func:`get_deps`. Each real package is imported
when it exists and exposes the §14.2 functions this lane needs; otherwise the signature-exact stub from
``topskill.testing.stubs`` is used and a warning is logged once. On 2026-10-17 the stub fallback branch
below is deleted; nothing else changes.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from functools import lru_cache
from types import ModuleType
from typing import Any

from topskill.testing.stubs import (
    StubBilling,
    StubKnowledge,
    StubRecords,
    stub_billing,
    stub_knowledge,
    stub_records,
)

log = logging.getLogger(__name__)

# The §14.2 names chat/a2a call. A package missing any of them is treated as "not built yet".
REQUIRED: dict[str, tuple[str, ...]] = {
    "billing": ("get_or_open_wallet", "preflight_round", "settle_round", "preflight_a2a", "settle_a2a_call"),
    "knowledge": ("has_ready_files", "search", "new_collector", "make_search_tool"),
    "records": ("validate_schema", "prompt_section", "make_save_record_tool"),
}


@dataclass(frozen=True)
class Deps:
    billing: ModuleType | StubBilling
    knowledge: ModuleType | StubKnowledge
    records: ModuleType | StubRecords


def is_stub(dep: Any) -> bool:
    """True when ``dep`` is a stub (callers skip optional work such as record extraction)."""
    return bool(getattr(dep, "is_stub", False))


def _load(name: str, stub: Any) -> Any:
    module_name = f"topskill.{name}"
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        # Stub fallback (delete on 2026-10-17).
        log.warning("deps_stub package=%s reason=%r", module_name, exc)
        return stub
    missing = [attr for attr in REQUIRED[name] if not hasattr(module, attr)]
    if missing:
        log.warning("deps_stub package=%s missing=%s", module_name, ",".join(missing))
        return stub
    return module


_override: Deps | None = None


@lru_cache(maxsize=1)
def _resolved() -> Deps:
    return Deps(
        billing=_load("billing", stub_billing),
        knowledge=_load("knowledge", stub_knowledge),
        records=_load("records", stub_records),
    )


def get_deps() -> Deps:
    """Real package if importable (and complete), else the stub (warning logged); cached."""
    return _override if _override is not None else _resolved()


def set_deps(deps: Deps | None) -> None:
    """Tests only: replace the resolved dependencies (``None`` restores the real resolution)."""
    global _override
    _override = deps
