"""The only place publish reaches billing and records (SPEC §3.1 dependency direction:
``publish → billing (get_or_open_wallet), records (validate_schema, assert_schema_change_allowed)``).

Each accessor returns the real package when it is importable, else the matching ``testing.stubs`` instance
(warning logged once), exactly like ``chat/deps.py``. Delete the fallback branch at integration time.
"""

from __future__ import annotations

import importlib
import logging
from functools import lru_cache
from types import ModuleType
from typing import Any

log = logging.getLogger(__name__)


def _load(module_name: str, stub_attr: str) -> ModuleType | Any:
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
        from topskill.testing import stubs

        log.warning("publish_dependency_stubbed module=%s", module_name)
        return getattr(stubs, stub_attr)


@lru_cache(maxsize=1)
def billing() -> ModuleType | Any:
    """``topskill.billing`` (``get_or_open_wallet``)."""
    return _load("topskill.billing", "stub_billing")


@lru_cache(maxsize=1)
def records() -> ModuleType | Any:
    """``topskill.records`` (``validate_schema``, ``assert_schema_change_allowed``)."""
    return _load("topskill.records", "stub_records")
