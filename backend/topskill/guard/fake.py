"""A deterministic guard backend for tests (installed with ``guard.set_backend(FakeGuard())``).

Rules (case-insensitive):

* ``ignore all previous instructions`` → ``pi_and_jailbreak``
* ``[[rai:<type>]]`` → ``rai:<type>``; ``[[csam]]`` → ``csam``
* a 16-digit card-like number (``4111 1111 1111 1111``) → ``sdp`` with a located CREDIT_CARD_NUMBER finding
* ``[[sdp-unlocated]]`` → ``sdp`` without a location
* ``[[guard-error]]`` → raises ``GuardUnavailable``; ``[[guard-slow]]`` → sleeps 30 s (timeout path)
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from topskill.guard.model_armor import GuardUnavailable
from topskill.guard.types import Finding, ScreenKind, ScreenResult

INJECTION = "ignore all previous instructions"
CARD = "4111 1111 1111 1111"
_CARD_RE = re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")
_RAI_RE = re.compile(r"\[\[rai:([a-z_]+)\]\]", re.I)


@dataclass
class FakeGuard:
    calls: list[tuple[ScreenKind, str]] = field(default_factory=list)

    async def screen(self, kind: ScreenKind, text: str) -> ScreenResult:
        self.calls.append((kind, text))
        lower = text.lower()
        if "[[guard-error]]" in lower:
            raise GuardUnavailable("HTTP 503: fake outage")
        if "[[guard-slow]]" in lower:
            await asyncio.sleep(30)
        categories: list[str] = []
        findings: list[Finding] = []
        if INJECTION in lower:
            categories.append("pi_and_jailbreak")
        categories.extend(f"rai:{m.lower()}" for m in _RAI_RE.findall(text))
        if "[[csam]]" in lower:
            categories.append("csam")
        cards = [Finding("CREDIT_CARD_NUMBER", m.start(), m.end()) for m in _CARD_RE.finditer(text)]
        if cards or "[[sdp-unlocated]]" in lower:
            categories.append("sdp")
            findings.extend(cards)
        return ScreenResult(categories=tuple(categories), findings=tuple(findings))

    def kinds(self) -> list[ScreenKind]:
        return [k for k, _ in self.calls]
