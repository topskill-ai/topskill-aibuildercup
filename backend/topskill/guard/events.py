"""The ``guard`` SSE event of the web chat stream (SPEC §18.2).

Emitted at most once per target and turn, only when a verdict changes something:

* ``{target: "prompt", action: "blocked" | "redacted", categories, text}`` — right after ``status``;
  ``text`` is the visitor message as stored (sensitive spans replaced).
* ``{target: "answer", action: "blocked" | "redacted", categories, text}`` — after the last
  ``text_delta`` and before ``citations``; the client replaces the streamed answer with ``text`` (``""``
  when blocked).

Clients that do not know the event ignore it (SPEC §7.1).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from topskill.guard.types import GuardVerdict

GUARD_EVENT = "guard"


class GuardData(BaseModel):
    target: Literal["prompt", "answer"]
    action: Literal["blocked", "redacted"]
    categories: list[str]
    text: str


def guard_frame(target: Literal["prompt", "answer"], verdict: GuardVerdict) -> tuple[str, GuardData]:
    text = "" if (target == "answer" and verdict.blocked) else verdict.text
    return (
        GUARD_EVENT,
        GuardData(
            target=target,
            action="blocked" if verdict.blocked else "redacted",
            categories=list(verdict.categories),
            text=text,
        ),
    )
