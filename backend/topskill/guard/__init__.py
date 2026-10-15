"""Model Armor protection for public agents (P2, SPEC §18 "Guard").

``sanitize_prompt(text, ctx)`` / ``sanitize_response(text, ctx)`` return a :class:`GuardVerdict`
(``allow`` / ``redact`` / ``block`` with the matched categories) and never raise (fail-open).
"""

from topskill.guard.events import GUARD_EVENT, GuardData, guard_frame
from topskill.guard.redact import redact, split_findings
from topskill.guard.service import (
    decide,
    get_backend,
    is_active,
    sanitize_prompt,
    sanitize_response,
    screening_enabled,
    set_backend,
    warm_up,
)
from topskill.guard.stats import STATS_KEYS, guard_stats_wire
from topskill.guard.types import Finding, GuardContext, GuardVerdict, ScreenResult

__all__ = [
    "GUARD_EVENT",
    "STATS_KEYS",
    "Finding",
    "GuardContext",
    "GuardData",
    "GuardVerdict",
    "ScreenResult",
    "decide",
    "get_backend",
    "guard_frame",
    "guard_stats_wire",
    "is_active",
    "redact",
    "sanitize_prompt",
    "sanitize_response",
    "screening_enabled",
    "set_backend",
    "split_findings",
    "warm_up",
]
