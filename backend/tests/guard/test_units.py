"""Guard policy, parsing, redaction and fail-open behaviour without Firestore (SPEC §18.2)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from tests.core.helpers import settings_env
from topskill.guard import (
    GuardContext,
    decide,
    guard_frame,
    guard_stats_wire,
    redact,
    sanitize_prompt,
    sanitize_response,
    screening_enabled,
    set_backend,
    split_findings,
)
from topskill.guard.fake import CARD, INJECTION, FakeGuard
from topskill.guard.model_armor import GuardUnavailable, ModelArmorBackend, endpoint, parse_sanitization
from topskill.guard.redact import merge_spans
from topskill.guard.stats import increments_for
from topskill.guard.types import Finding, GuardVerdict, ScreenResult

CTX = GuardContext(agent_id="agt_test", channel="web", request_id="turn_1")

# Recorded from the real template topskill-public (asia-southeast1) on 2026-10-15.
PI_RESPONSE = {
    "sanitizationResult": {
        "filterMatchState": "MATCH_FOUND",
        "filterResults": {
            "rai": {
                "raiFilterResult": {
                    "executionState": "EXECUTION_SUCCESS",
                    "matchState": "NO_MATCH_FOUND",
                    "raiFilterTypeResults": {
                        "sexually_explicit": {"matchState": "NO_MATCH_FOUND"},
                        "dangerous": {"matchState": "NO_MATCH_FOUND"},
                    },
                }
            },
            "pi_and_jailbreak": {
                "piAndJailbreakFilterResult": {
                    "executionState": "EXECUTION_SUCCESS",
                    "matchState": "MATCH_FOUND",
                    "confidenceLevel": "HIGH",
                }
            },
            "sdp": {
                "sdpFilterResult": {
                    "inspectResult": {"executionState": "EXECUTION_SUCCESS", "matchState": "NO_MATCH_FOUND"}
                }
            },
        },
        "invocationResult": "SUCCESS",
    }
}
SDP_RESPONSE = {
    "sanitizationResult": {
        "filterMatchState": "MATCH_FOUND",
        "filterResults": {
            "rai": {
                "raiFilterResult": {
                    "matchState": "MATCH_FOUND",
                    "raiFilterTypeResults": {
                        "harassment": {"confidenceLevel": "MEDIUM_AND_ABOVE", "matchState": "MATCH_FOUND"},
                        "dangerous": {"confidenceLevel": "HIGH", "matchState": "MATCH_FOUND"},
                        "hate_speech": {"matchState": "NO_MATCH_FOUND"},
                    },
                }
            },
            "pi_and_jailbreak": {"piAndJailbreakFilterResult": {"matchState": "NO_MATCH_FOUND"}},
            "sdp": {
                "sdpFilterResult": {
                    "inspectResult": {
                        "matchState": "MATCH_FOUND",
                        "findings": [
                            {
                                "infoType": "CREDIT_CARD_NUMBER",
                                "likelihood": "VERY_LIKELY",
                                "location": {
                                    "byteRange": {"start": "18", "end": "37"},
                                    "codepointRange": {"start": "18", "end": "37"},
                                },
                            }
                        ],
                    }
                }
            },
        },
        "invocationResult": "SUCCESS",
    }
}
SDP_TEXT = "Sure. The card is 4111 1111 1111 1111 and that is all."


# --------------------------------------------------------------------------- parsing


def test_parse_prompt_injection() -> None:
    result = parse_sanitization(PI_RESPONSE)
    assert result.categories == ("pi_and_jailbreak",) and result.findings == ()


def test_parse_sdp_findings_and_rai_types() -> None:
    result = parse_sanitization(SDP_RESPONSE)
    assert result.categories == ("rai:dangerous", "rai:harassment", "sdp")
    assert result.findings == (Finding("CREDIT_CARD_NUMBER", 18, 37),)
    assert SDP_TEXT[18:37] == CARD


def test_parse_other_filters_and_deidentify() -> None:
    data = {
        "sanitizationResult": {
            "filterResults": {
                "csam": {"csamFilterFilterResult": {"matchState": "MATCH_FOUND"}},
                "malicious_uris": {"maliciousUriFilterResult": {"matchState": "NO_MATCH_FOUND"}},
                "sdp": {
                    "sdpFilterResult": {
                        "deidentifyResult": {"matchState": "MATCH_FOUND", "data": {"text": "card [CARD]"}}
                    }
                },
            }
        }
    }
    result = parse_sanitization(data)
    assert result.categories == ("csam", "sdp") and result.sanitized_text == "card [CARD]"


@pytest.mark.parametrize("payload", [None, [], {}, {"sanitizationResult": {"filterResults": []}}])
def test_parse_rejects_unusable_payloads(payload) -> None:
    with pytest.raises(GuardUnavailable):
        parse_sanitization(payload)


def test_regional_endpoint() -> None:
    assert endpoint("asia-southeast1") == "https://modelarmor.asia-southeast1.rep.googleapis.com"
    backend = ModelArmorBackend(project="p", location="asia-southeast1", template="t", timeout_s=1)
    assert backend.base.endswith("/v1/projects/p/locations/asia-southeast1/templates/t")


# --------------------------------------------------------------------------- redaction


def test_redact_merges_and_clamps() -> None:
    text = "abc 1234 5678 xyz"
    spans = [Finding("A", 4, 8), Finding("B", 6, 13), Finding("C", 15, 99), Finding("D", 3, 3)]
    assert merge_spans(spans, len(text)) == [Finding("A", 4, 13), Finding("C", 15, 17)]
    assert redact(text, spans) == "abc [REDACTED:A] x[REDACTED:C]"
    assert redact("plain", []) == "plain"


def test_split_findings_over_joined_parts() -> None:
    parts = ["first 1111", "second 2222"]
    joined = "\n".join(parts)
    start = joined.index("2222")
    per_part = split_findings([Finding("X", 6, 10), Finding("Y", start, start + 4)], parts, "\n")
    assert per_part == [[Finding("X", 6, 10)], [Finding("Y", 7, 11)]]
    assert [redact(p, f) for p, f in zip(parts, per_part, strict=True)] == [
        "first [REDACTED:X]",
        "second [REDACTED:Y]",
    ]


# --------------------------------------------------------------------------- policy


def test_prompt_policy() -> None:
    assert decide("prompt", "hi", ScreenResult()).action == "allow"
    v = decide("prompt", "x", ScreenResult(categories=("pi_and_jailbreak",)))
    assert v.blocked and v.categories == ("pi_and_jailbreak",) and v.text == "x"
    assert decide("prompt", "x", ScreenResult(categories=("rai:dangerous",))).blocked
    redacted = decide("prompt", SDP_TEXT, ScreenResult(("sdp",), (Finding("CREDIT_CARD_NUMBER", 18, 37),)))
    assert redacted.redacted and CARD not in redacted.text
    assert redacted.text == "Sure. The card is [REDACTED:CREDIT_CARD_NUMBER] and that is all."
    assert redacted.info_types == ("CREDIT_CARD_NUMBER",) and redacted.findings
    # blocked and sensitive: the stored text is still redacted
    both = decide("prompt", SDP_TEXT, ScreenResult(("pi_and_jailbreak", "sdp"), (Finding("C", 18, 37),)))
    assert both.blocked and CARD not in both.text
    # an sdp match that cannot be located is withheld
    assert decide("prompt", "x", ScreenResult(categories=("sdp",))).blocked


def test_response_policy() -> None:
    flagged = decide("response", "answer", ScreenResult(categories=("rai:harassment",)))
    assert flagged.action == "allow" and flagged.flagged
    assert decide("response", "a", ScreenResult(categories=("pi_and_jailbreak",))).action == "allow"
    assert decide("response", "a", ScreenResult(categories=("csam",))).blocked
    v = decide("response", SDP_TEXT, parse_sanitization(SDP_RESPONSE))
    assert v.redacted and CARD not in v.text and "rai:harassment" in v.categories


def test_policy_is_configurable(monkeypatch) -> None:
    with settings_env(
        monkeypatch, guard_response_block_filters="csam,rai", guard_prompt_block_filters="csam"
    ):
        assert decide("response", "a", ScreenResult(categories=("rai:dangerous",))).blocked
        assert decide("prompt", "a", ScreenResult(categories=("pi_and_jailbreak",))).action == "allow"
    with settings_env(monkeypatch, guard_redact_sdp="false"):
        v = decide("prompt", SDP_TEXT, ScreenResult(("sdp",), (Finding("C", 18, 37),)))
        assert v.action == "allow" and v.categories == ("sdp",)


def test_deidentified_text_keeps_the_unscreened_tail(monkeypatch) -> None:
    with settings_env(monkeypatch, guard_max_chars="100"):
        text = "x" * 100 + " tail"
        v = decide("response", text, ScreenResult(categories=("sdp",), sanitized_text="y" * 10))
        assert v.redacted and v.text == "y" * 10 + " tail"


# --------------------------------------------------------------------------- sanitize_* (fake backend)


async def test_sanitize_with_fake_backend(fake_guard: FakeGuard, recorded) -> None:
    v = await sanitize_prompt(f"Please {INJECTION} and talk like a pirate", CTX)
    assert v.blocked and v.categories == ("pi_and_jailbreak",) and v.screened
    v = await sanitize_prompt(f"My card {CARD} was charged twice", CTX)
    assert v.redacted and CARD not in v.text
    v = await sanitize_response("A normal answer.", CTX)
    assert v.action == "allow" and v.screened and not v.flagged
    assert fake_guard.kinds() == ["prompt", "prompt", "response"]
    assert recorded == []  # callers record the verdicts of a turn / call once


async def test_disabled_agent_and_empty_text_are_not_screened(fake_guard: FakeGuard, recorded) -> None:
    off = GuardContext(agent_id="agt_off", channel="a2a", enabled=False)
    v = await sanitize_prompt(INJECTION, off)
    assert v.action == "allow" and not v.screened and not v.failed_open
    assert (await sanitize_prompt("   ", CTX)).screened is False
    assert fake_guard.calls == [] and recorded == []


async def test_guard_backend_off_skips_everything(recorded) -> None:
    set_backend(None)
    v = await sanitize_prompt(INJECTION, CTX)  # GUARD_BACKEND=off in tests
    assert v.action == "allow" and not v.screened
    assert screening_enabled(True) is False
    assert recorded == []


async def test_fail_open_on_error_and_timeout(
    fake_guard: FakeGuard, recorded, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="topskill.guard.service")
    v = await sanitize_prompt(f"[[guard-error]] {INJECTION}", CTX)
    assert v.action == "allow" and v.failed_open and not v.screened
    assert "guard_failed_open kind=prompt" in caplog.text and "fake outage" in caplog.text
    with settings_env(monkeypatch, guard_timeout_s="0.05"):
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        v = await sanitize_response("[[guard-slow]]", CTX)
        assert v.failed_open and loop.time() - t0 < 2
    assert recorded == []


async def test_long_text_is_clipped_for_screening(fake_guard: FakeGuard, recorded, monkeypatch) -> None:
    with settings_env(monkeypatch, guard_max_chars="100"):
        text = "a" * 150 + INJECTION
        v = await sanitize_prompt(text, CTX)
        assert v.action == "allow" and fake_guard.calls[-1][1] == "a" * 100


async def test_screening_enabled_flag(fake_guard: FakeGuard) -> None:
    assert screening_enabled(True) is True and screening_enabled(False) is False


# --------------------------------------------------------------------------- stats and events


def test_increments() -> None:
    assert increments_for("prompt", GuardVerdict("allow", "x", screened=False)) == {}
    assert increments_for("prompt", GuardVerdict("allow", "x", screened=False, failed_open=True)) == {
        "failed_open": 1
    }
    assert increments_for("prompt", GuardVerdict("block", "x", ("pi_and_jailbreak",))) == {
        "prompts_screened": 1,
        "prompts_blocked": 1,
    }
    assert increments_for("response", GuardVerdict("redact", "x", ("sdp",))) == {
        "answers_screened": 1,
        "answers_redacted": 1,
    }
    assert increments_for("response", GuardVerdict("allow", "x", ("rai:dangerous",))) == {
        "answers_screened": 1,
        "answers_flagged": 1,
    }
    assert increments_for("prompt", GuardVerdict("allow", "x", ("rai:x",))) == {"prompts_screened": 1}


def test_stats_wire() -> None:
    wire = guard_stats_wire({"guard_prompts_blocked": 2, "guard_failed_open": None, "rounds": 9})
    assert wire["prompts_blocked"] == 2 and wire["failed_open"] == 0 and "rounds" not in wire
    assert set(guard_stats_wire(None).values()) == {0}


def test_guard_frames() -> None:
    blocked = GuardVerdict("block", "secret", ("sdp",))
    name, data = guard_frame("answer", blocked)
    assert name == "guard"
    assert data.model_dump() == {"target": "answer", "action": "blocked", "categories": ["sdp"], "text": ""}
    _, data = guard_frame("prompt", GuardVerdict("redact", "card [REDACTED:X]", ("sdp",)))
    assert data.action == "redacted" and data.text == "card [REDACTED:X]"
