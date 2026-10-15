"""Value types of the guard package (SPEC §18.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

ScreenKind = Literal["prompt", "response"]
Action = Literal["allow", "redact", "block"]
Channel = Literal["web", "a2a"]


@dataclass(frozen=True)
class Finding:
    """A sensitive-data finding with its codepoint range in the screened text (Python ``str`` indexes)."""

    info_type: str
    start: int
    end: int


@dataclass(frozen=True)
class ScreenResult:
    """What a backend reported for one text, before our policy is applied.

    ``categories`` are the matched filters: ``pi_and_jailbreak``, ``malicious_uris``, ``csam``, ``sdp`` and
    ``rai:<type>`` (e.g. ``rai:dangerous``). ``findings`` are SDP inspect findings with a location;
    ``sanitized_text`` is the de-identified text when the template uses an SDP de-identify template.
    """

    categories: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    sanitized_text: str | None = None


class GuardBackend(Protocol):
    async def screen(self, kind: ScreenKind, text: str) -> ScreenResult: ...


@dataclass(frozen=True)
class GuardContext:
    """Who is being screened. ``enabled`` is the agent's ``config.safety_screening`` flag."""

    agent_id: str
    channel: Channel
    enabled: bool = True
    request_id: str | None = None


@dataclass(frozen=True)
class GuardVerdict:
    """The decision for one text.

    * ``allow``: use ``text`` unchanged (also when screening was skipped or failed open).
    * ``redact``: use ``text``, which has the sensitive spans replaced.
    * ``block``: do not run the model / do not show the answer. ``text`` is the safest stored form of the
      input (sensitive spans redacted when the backend located them).

    ``categories`` lists every matched filter (also those that did not trigger an action, see
    ``flagged``). ``screened`` is False when no screening happened (disabled, empty text, failed open).
    """

    action: Action
    text: str
    categories: tuple[str, ...] = ()
    screened: bool = True
    failed_open: bool = False
    info_types: tuple[str, ...] = field(default=())
    findings: tuple[Finding, ...] = field(default=())  # spans redacted in ``text`` (input coordinates)

    @property
    def blocked(self) -> bool:
        return self.action == "block"

    @property
    def redacted(self) -> bool:
        return self.action == "redact"

    @property
    def flagged(self) -> bool:
        """Matched something without an action (e.g. an RAI match on an answer)."""
        return self.action == "allow" and bool(self.categories)


def family(category: str) -> str:
    """``rai:dangerous`` → ``rai``."""
    return category.split(":", 1)[0]
