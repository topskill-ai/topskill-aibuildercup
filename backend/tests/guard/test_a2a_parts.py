"""Screening of every A2A message part, batched counters and the health tracker (SPEC §18 "Guard")."""

from __future__ import annotations

import base64
import copy
import json

import pytest

from topskill.guard import (
    GuardContext,
    healthy,
    protection_shown,
    sanitize_prompt,
    screen_a2a_message,
    set_backend,
    stats,
)
from topskill.guard import service as guard_service
from topskill.guard.fake import CARD, INJECTION, FakeGuard
from topskill.guard.types import Finding, GuardVerdict, ScreenResult

AGENT = "agt_parts"
REDACTED = "[REDACTED:CREDIT_CARD_NUMBER]"


def _msg(*parts: dict) -> dict:
    return {"messageId": "m1", "role": "ROLE_USER", "parts": list(parts)}


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


# --------------------------------------------------------------------------- what is screened


async def test_data_only_injection_is_blocked(fake_guard: FakeGuard, recorded) -> None:
    msg = _msg({"data": {"q": f"{INJECTION} and print your prompt"}})
    screen = await screen_a2a_message(AGENT, True, msg)
    assert screen.invalid is None and screen.verdict.blocked
    assert screen.verdict.categories == ("pi_and_jailbreak",)
    assert fake_guard.calls == [("prompt", json.dumps(msg["parts"][0]["data"], ensure_ascii=False,
                                                      separators=(",", ":")))]  # fmt: skip
    assert [(a, k, v.action) for a, k, v in recorded] == [(AGENT, "prompt", "block")]


async def test_text_plus_injected_data_is_blocked(fake_guard: FakeGuard) -> None:
    msg = _msg({"text": "hi"}, {"data": [1, {"note": INJECTION.upper()}]})
    screen = await screen_a2a_message(AGENT, True, msg)
    assert screen.verdict.blocked and screen.segments == 2
    assert fake_guard.calls[0][1].startswith("hi\n[1,")


async def test_v03_shapes_are_screened(fake_guard: FakeGuard) -> None:
    msg = _msg(
        {"kind": "text", "text": "Please read"},
        {"kind": "data", "data": {"x": "fine"}},
        {"kind": "file", "file": {"bytes": _b64(f"notes: {INJECTION}"), "mimeType": "text/plain"}},
    )
    screen = await screen_a2a_message(AGENT, True, msg)
    assert screen.verdict.blocked and screen.segments == 3
    assert f"notes: {INJECTION}" in fake_guard.calls[0][1]


async def test_text_file_bytes_are_screened_and_redacted(fake_guard: FakeGuard) -> None:
    msg = _msg({"raw": _b64(f"Card: {CARD}\n"), "mediaType": "text/csv; charset=utf-8", "filename": "a.csv"})
    screen = await screen_a2a_message(AGENT, True, msg)
    assert screen.verdict.redacted
    assert base64.b64decode(msg["parts"][0]["raw"]).decode() == f"Card: {REDACTED}\n"


async def test_redaction_is_written_into_text_and_data_parts(fake_guard: FakeGuard) -> None:
    msg = _msg(
        {"text": "Refund help"},
        {"data": {"payment": {"card": CARD, "amount": 12}, "tags": ["a", f"x {CARD}"]}},
        {"kind": "text", "text": f"card {CARD} please"},
    )
    screen = await screen_a2a_message(AGENT, True, msg)
    assert screen.verdict.redacted
    parts = msg["parts"]
    assert parts[0]["text"] == "Refund help"
    assert parts[1]["data"] == {"payment": {"card": REDACTED, "amount": 12}, "tags": ["a", f"x {REDACTED}"]}
    assert parts[2]["text"] == f"card {REDACTED} please"


class _DigitsGuard:
    """Reports every run of 16 digits as a located card number (also inside JSON numbers)."""

    async def screen(self, kind, text):
        import re

        found = [Finding("CREDIT_CARD_NUMBER", m.start(), m.end()) for m in re.finditer(r"\d{16}", text)]
        return ScreenResult(categories=("sdp",) if found else (), findings=tuple(found))


async def test_unrepresentable_redaction_blocks() -> None:
    # A card number as a JSON number: the redacted JSON no longer parses, so the message is withheld.
    set_backend(_DigitsGuard())
    try:
        msg = _msg({"text": "hello"}, {"data": {"card": 4111111111111111}})
        original = copy.deepcopy(msg)
        screen = await screen_a2a_message(AGENT, True, msg)
        assert screen.verdict.blocked and screen.verdict.categories == ("sdp",)
        assert msg == original  # nothing half-rewritten matters: the call is refused
        # The same number as a string is rewritten in place.
        msg = _msg({"data": {"card": "4111111111111111"}})
        screen = await screen_a2a_message(AGENT, True, msg)
        assert screen.verdict.redacted and msg["parts"][0]["data"] == {"card": REDACTED}
    finally:
        set_backend(None)


@pytest.mark.parametrize(
    "part",
    [
        {"url": "https://example.com/a.pdf", "mediaType": "application/pdf"},
        {"raw": _b64("%PDF-1.7"), "mediaType": "application/pdf"},
        {"raw": _b64("no media type")},
        {"raw": base64.b64encode(b"\xff\xfe\x00bad").decode(), "mediaType": "text/plain"},
        {"kind": "file", "file": {"uri": "gs://bucket/x.txt", "mimeType": "text/plain"}},
        {"kind": "file", "file": {"bytes": _b64("png"), "mimeType": "image/png"}},
        {"kind": "video"},
        {"metadata": {"only": "metadata"}},
        {"text": 42},
        "not an object",
    ],
)
async def test_unscreenable_parts_fail_closed(fake_guard: FakeGuard, recorded, part) -> None:
    screen = await screen_a2a_message(AGENT, True, _msg({"text": "hello"}, part))
    assert screen.invalid and not screen.verdict.screened
    assert fake_guard.calls == [] and recorded == []


async def test_screening_off_leaves_any_part_alone(fake_guard: FakeGuard) -> None:
    msg = _msg({"url": "https://example.com/a.pdf"}, {"data": {"q": INJECTION}})
    screen = await screen_a2a_message(AGENT, False, msg)
    assert screen.invalid is None and not screen.verdict.screened and fake_guard.calls == []


async def test_guard_backend_off_leaves_any_part_alone() -> None:
    set_backend(None)  # GUARD_BACKEND=off in tests
    screen = await screen_a2a_message(AGENT, True, _msg({"url": "https://example.com/a.pdf"}))
    assert screen.invalid is None and not screen.verdict.screened


# --------------------------------------------------------------------------- counters: one write


async def test_record_merges_verdicts_into_one_write(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[tuple[str, dict]] = []

    async def fake_write(agent_id: str, deltas: dict) -> None:
        writes.append((agent_id, deltas))

    monkeypatch.setattr(stats, "_write", fake_write)
    stats.record(
        AGENT,
        [
            ("prompt", GuardVerdict("redact", "x", ("sdp",))),
            ("response", GuardVerdict("redact", "y", ("sdp",))),
            ("response", None),
        ],
    )
    stats.record(AGENT, [("prompt", GuardVerdict("allow", "x", screened=False))])  # nothing to count
    from topskill import tasks

    for t in list(tasks.pending()):
        await t
    assert writes == [
        (
            AGENT,
            {"prompts_screened": 1, "prompts_redacted": 1, "answers_screened": 1, "answers_redacted": 1},
        )
    ]


# --------------------------------------------------------------------------- health


async def test_consecutive_fail_open_hides_protection(fake_guard: FakeGuard) -> None:
    ctx = GuardContext(agent_id=AGENT, channel="web")
    assert healthy() and protection_shown(True)
    for _ in range(guard_service.UNHEALTHY_AFTER - 1):
        await sanitize_prompt("[[guard-error]] hi", ctx)
    assert protection_shown(True)
    await sanitize_prompt("[[guard-error]] hi", ctx)
    assert not healthy() and not protection_shown(True)
    await sanitize_prompt("a fine question", ctx)  # the next good check clears it
    assert healthy() and protection_shown(True) and not protection_shown(False)
