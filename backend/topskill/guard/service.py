"""Screening entry points and policy (SPEC §18 "Guard").

``sanitize_prompt`` / ``sanitize_response`` never raise: a disabled agent, ``GUARD_BACKEND=off``, empty text
or any backend failure (timeout, HTTP error, auth error) yields an ``allow`` verdict. Failures are logged
as ``guard_failed_open`` with the request's trace id and counted (``failed_open``). Fail-open is the
documented choice: Model Armor is an extra layer on top of Gemini's own safety filters, and an outage must
not take every public agent down.

Policy (configurable, see ``config.Settings.guard_*``):

* prompt: a match in ``GUARD_PROMPT_BLOCK_FILTERS`` (default ``pi_and_jailbreak, rai, malicious_uris,
  csam``) blocks; otherwise an ``sdp`` match is redacted (``GUARD_REDACT_SDP``) so the model and the
  transcript never see the value.
* response: a match in ``GUARD_RESPONSE_BLOCK_FILTERS`` (default ``csam``) blocks; an ``sdp`` match is
  redacted; any other match is only counted as ``answers_flagged`` (RAI filters on model answers gave false
  positives in the 2026-10-15 probe, e.g. ``harassment`` on a plain sentence containing a card number).
* an ``sdp`` match without locations (and no de-identified text) cannot be redacted, so it blocks.

The functions do not write counters: callers pass every verdict of a turn / call to ``stats.record`` once.
A small in-process health tracker counts consecutive fail-open results; while it is tripped
(``UNHEALTHY_AFTER`` in a row, e.g. the IAM grant is missing) :func:`protection_shown` is False, so the public
badge and the creator's "Model Armor on" chip stop claiming protection. Screening is still attempted on every
message, and the first successful check clears it.
"""

from __future__ import annotations

import asyncio
import logging

from topskill.config import get_settings
from topskill.errors import current_trace_id
from topskill.guard.model_armor import ModelArmorBackend
from topskill.guard.redact import merge_spans, redact
from topskill.guard.types import (
    Action,
    GuardBackend,
    GuardContext,
    GuardVerdict,
    ScreenKind,
    ScreenResult,
    family,
)

log = logging.getLogger(__name__)

UNHEALTHY_AFTER = 3  # consecutive fail-open results that hide the "protected" claims

_override: GuardBackend | None = None
_consecutive_failures = 0
_backend: GuardBackend | None = None
_backend_key: tuple | None = None


def set_backend(backend: GuardBackend | None) -> None:
    """Tests only: replace the backend (``None`` restores the configured one); also resets the health."""
    global _override, _consecutive_failures
    _override = backend
    _consecutive_failures = 0


def _note_health(ok: bool) -> None:
    global _consecutive_failures
    _consecutive_failures = 0 if ok else _consecutive_failures + 1


def healthy() -> bool:
    """False after ``UNHEALTHY_AFTER`` consecutive fail-open results (cleared by the next good check)."""
    return _consecutive_failures < UNHEALTHY_AFTER


def is_active() -> bool:
    """True when screening can run at all (a test backend is set, or ``GUARD_BACKEND`` is not ``off``)."""
    return _override is not None or get_settings().guard_backend != "off"


def get_backend() -> GuardBackend | None:
    global _backend, _backend_key
    if _override is not None:
        return _override
    s = get_settings()
    if s.guard_backend == "off":
        return None
    key = (s.guard_project, s.guard_location, s.guard_template, s.guard_timeout_s)
    if _backend is None or _backend_key != key:
        _backend = ModelArmorBackend(
            project=s.guard_project,
            location=s.guard_location,
            template=s.guard_template,
            timeout_s=s.guard_timeout_s,
        )
        _backend_key = key
    return _backend


async def warm_up() -> None:
    """Load ADC and fetch a token at startup, so the first screened turn does not pay ~2 s for it (which
    would exceed ``GUARD_TIMEOUT_S`` and fail open). Never raises."""
    backend = get_backend() if _override is None else None
    warm = getattr(backend, "warm_up", None)
    if warm is None:
        return
    try:
        await warm()
        log.info("guard_warm_up_done")
    except Exception as exc:
        log.warning("guard_warm_up_failed error=%s", f"{type(exc).__name__}: {str(exc)[:200]}")


def screening_enabled(safety_screening: bool) -> bool:
    """True when screening is attempted for an agent with this flag."""
    return bool(safety_screening) and is_active()


def protection_shown(safety_screening: bool) -> bool:
    """What the public page shows as "Protected by Model Armor" and the Studio as "Model Armor on":
    screening is attempted and the recent checks did not all fail open."""
    return screening_enabled(safety_screening) and healthy()


def decide(kind: ScreenKind, text: str, result: ScreenResult) -> GuardVerdict:
    """Apply the policy to a backend result (``text`` is the full text; offsets refer to its prefix)."""
    s = get_settings()
    blockers = set(s.guard_prompt_block_filters if kind == "prompt" else s.guard_response_block_filters)
    categories = tuple(dict.fromkeys(result.categories))
    families = {family(c) for c in categories}
    info_types = tuple(dict.fromkeys(f.info_type for f in result.findings if f.info_type))

    safe_text, spans = text, ()
    if "sdp" in families:
        if result.findings:
            spans = tuple(merge_spans(result.findings, len(text)))
            safe_text = redact(text, spans)
        elif result.sanitized_text is not None:
            screened_len = min(len(text), s.guard_max_chars)
            safe_text = result.sanitized_text + text[screened_len:]

    def verdict(action: Action, out: str) -> GuardVerdict:
        return GuardVerdict(
            action, out, categories, info_types=info_types, findings=spans if out != text else ()
        )

    if families & blockers:
        return verdict("block", safe_text)
    if "sdp" in families:
        if s.guard_redact_sdp and safe_text != text:
            return verdict("redact", safe_text)
        if s.guard_redact_sdp:  # matched but nothing could be located: cannot redact, so withhold
            return verdict("block", text)
    return verdict("allow", text)


async def _sanitize(kind: ScreenKind, text: str, ctx: GuardContext) -> GuardVerdict:
    skipped = GuardVerdict("allow", text, screened=False)
    try:
        backend = get_backend() if ctx.enabled else None
        if backend is None or not text.strip():
            return skipped
        s = get_settings()
        clipped = text[: s.guard_max_chars]
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            async with asyncio.timeout(s.guard_timeout_s):
                result = await backend.screen(kind, clipped)
        except Exception as exc:  # timeout, HTTP, auth, parse: fail open
            verdict = GuardVerdict("allow", text, screened=False, failed_open=True)
            log.warning(
                "guard_failed_open kind=%s channel=%s agent_id=%s request_id=%s trace_id=%s error=%s",
                kind,
                ctx.channel,
                ctx.agent_id,
                ctx.request_id,
                current_trace_id() or "-",
                f"{type(exc).__name__}: {str(exc)[:300]}",
            )
            _note_health(False)
            return verdict
        _note_health(True)
        verdict = decide(kind, text, result)
        log.info(
            "guard_verdict kind=%s channel=%s agent_id=%s request_id=%s action=%s categories=%s "
            "info_types=%s ms=%d",
            kind,
            ctx.channel,
            ctx.agent_id,
            ctx.request_id,
            verdict.action,
            ",".join(verdict.categories) or "-",
            ",".join(verdict.info_types) or "-",
            int((loop.time() - started) * 1000),
        )
        return verdict
    except Exception:  # a bug here must never break a turn
        log.exception("guard_internal_error kind=%s agent_id=%s", kind, ctx.agent_id)
        return GuardVerdict("allow", text, screened=False, failed_open=True)


async def sanitize_prompt(text: str, ctx: GuardContext) -> GuardVerdict:
    """Screen a visitor / caller message before the model runs. Never raises."""
    return await _sanitize("prompt", text, ctx)


async def sanitize_response(text: str, ctx: GuardContext) -> GuardVerdict:
    """Screen a final model answer before it is persisted and charged. Never raises."""
    return await _sanitize("response", text, ctx)
