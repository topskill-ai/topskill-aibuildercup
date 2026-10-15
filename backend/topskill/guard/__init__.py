"""Model Armor protection for public agents (P2, SPEC §18.2).

``sanitize_prompt(text, ctx)`` / ``sanitize_response(text, ctx)`` return a :class:`GuardVerdict`
(``allow`` / ``redact`` / ``block`` with the matched categories) and never raise (fail-open). Callers pass
every verdict of one turn / call to ``record_stats`` (one counter write). ``screen_a2a_message`` screens
all parts of an A2A message.
"""

from topskill.guard.a2a_parts import A2AScreen, screen_a2a_message
from topskill.guard.events import GUARD_EVENT, GuardData, guard_frame
from topskill.guard.redact import redact, split_findings
from topskill.guard.service import (
    decide,
    get_backend,
    healthy,
    is_active,
    protection_shown,
    sanitize_prompt,
    sanitize_response,
    screening_enabled,
    set_backend,
    warm_up,
)
from topskill.guard.stats import STATS_KEYS, guard_stats_wire
from topskill.guard.stats import record as record_stats
from topskill.guard.types import Finding, GuardContext, GuardVerdict, ScreenResult

__all__ = [
    "GUARD_EVENT",
    "STATS_KEYS",
    "A2AScreen",
    "Finding",
    "GuardContext",
    "GuardData",
    "GuardVerdict",
    "ScreenResult",
    "decide",
    "get_backend",
    "guard_frame",
    "guard_stats_wire",
    "healthy",
    "is_active",
    "protection_shown",
    "record_stats",
    "redact",
    "sanitize_prompt",
    "sanitize_response",
    "screen_a2a_message",
    "screening_enabled",
    "set_backend",
    "split_findings",
    "warm_up",
]
