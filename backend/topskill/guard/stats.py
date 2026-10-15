"""Guard verdict counters in ``agent_stats/{agent_id}`` (SPEC §18 "Guard").

Stored as flat ``guard_<key>`` fields, written only with ``firestore.Increment`` + ``set(merge=True)``
(the §5.1 rule for agent_stats: no read, no transaction contention). ``agent_stats/{agent_id}`` is the
agent's hottest document, so callers collect every verdict of one turn / call and :func:`record` writes them
in ONE fire-and-forget background task: a failed counter never affects a turn or a call.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from google.cloud import firestore

from topskill import tasks
from topskill.db import Col, get_db
from topskill.guard.types import GuardVerdict, ScreenKind

log = logging.getLogger(__name__)

STATS_KEYS = (
    "prompts_screened",
    "prompts_blocked",
    "prompts_redacted",
    "answers_screened",
    "answers_blocked",
    "answers_redacted",
    "answers_flagged",
    "failed_open",
)
FIELD_PREFIX = "guard_"


def increments_for(kind: ScreenKind, verdict: GuardVerdict) -> dict[str, int]:
    """Counter deltas of one verdict (empty when nothing was attempted)."""
    if verdict.failed_open:
        return {"failed_open": 1}
    if not verdict.screened:
        return {}
    prefix = "prompts" if kind == "prompt" else "answers"
    out = {f"{prefix}_screened": 1}
    if verdict.blocked:
        out[f"{prefix}_blocked"] = 1
    elif verdict.redacted:
        out[f"{prefix}_redacted"] = 1
    elif verdict.flagged and kind == "response":
        out["answers_flagged"] = 1
    return out


async def _write(agent_id: str, deltas: dict[str, int]) -> None:
    ref = get_db().collection(Col.AGENT_STATS).document(agent_id)
    await ref.set(
        {f"{FIELD_PREFIX}{k}": firestore.Increment(v) for k, v in deltas.items()},
        merge=True,
    )


def merged_increments(screens: Iterable[tuple[ScreenKind, GuardVerdict | None]]) -> dict[str, int]:
    """Summed counter deltas of several verdicts (``None`` entries are skipped)."""
    out: dict[str, int] = {}
    for kind, verdict in screens:
        if verdict is None:
            continue
        for key, value in increments_for(kind, verdict).items():
            out[key] = out.get(key, 0) + value
    return out


def record(agent_id: str, screens: Iterable[tuple[ScreenKind, GuardVerdict | None]]) -> None:
    """Schedule ONE counter update for all verdicts of a turn / call (no-op when nothing is counted)."""
    deltas = merged_increments(screens)
    if not deltas or not agent_id:
        return
    try:
        tasks.spawn(_write(agent_id, deltas), name=f"guard-stats:{agent_id}")
    except RuntimeError:  # no running loop (sync caller in a test)
        log.debug("guard_stats_skipped agent_id=%s", agent_id)


def guard_stats_wire(data: dict[str, Any] | None) -> dict[str, int]:
    """``AgentStats.guard`` (TS ``GuardStats``) from a stats doc; zeros when missing."""
    data = data or {}
    return {k: int(data.get(f"{FIELD_PREFIX}{k}") or 0) for k in STATS_KEYS}
