"""The only place publish reaches billing and records (SPEC §3.1 dependency direction:
``publish → billing (get_or_open_wallet), records (validate_schema, assert_schema_change_allowed)``).

The accessors return the real packages (the stub fallback was removed at the 2026-10-17 integration);
tests may monkeypatch them.
"""

from __future__ import annotations

from types import ModuleType


def billing() -> ModuleType:
    """``topskill.billing`` (``get_or_open_wallet``)."""
    from topskill import billing as module

    return module


def records() -> ModuleType:
    """``topskill.records`` (``validate_schema``, ``assert_schema_change_allowed``)."""
    from topskill import records as module

    return module
