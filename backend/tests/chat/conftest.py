from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from tests.chat.doubles import ChargingBilling, FakeKnowledge, FakeRecords
from topskill.chat import deps as chat_deps
from topskill.testing import fake_llm


@pytest.fixture(autouse=True)
def _fresh_fake_llm() -> Iterator[None]:
    fake_llm.reset()
    yield
    fake_llm.reset()


@pytest.fixture
def doubles() -> Iterator[tuple[ChargingBilling, FakeKnowledge, FakeRecords]]:
    billing, knowledge, records = ChargingBilling(), FakeKnowledge(), FakeRecords()
    chat_deps.set_deps(chat_deps.Deps(billing=billing, knowledge=knowledge, records=records))
    yield billing, knowledge, records
    chat_deps.set_deps(None)


def parse_sse(raw: str) -> list[tuple[str, dict]]:
    frames: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        lines = block.split("\n")
        assert lines[0].startswith("event: ") and lines[1].startswith("data: ") and len(lines) == 2, block
        frames.append((lines[0][7:], json.loads(lines[1][6:])))
    return frames


def assert_protocol(frames: list[tuple[str, dict]]) -> dict:
    """SPEC §7.4 rule 1: status first, done exactly once and last, error at most once right before done,
    citations at most once after the last text. Returns the done payload."""
    names = [n for n, _ in frames]
    assert names[0] == "status" and names.count("status") == 1
    assert names[-1] == "done" and names.count("done") == 1
    assert names.count("error") <= 1
    if "error" in names:
        assert names.index("error") == len(names) - 2
    if "citations" in names:
        assert names.count("citations") == 1
        idx = names.index("citations")
        assert all(n not in ("text_delta", "tool_start") for n in names[idx + 1 :])
    started = {d["id"] for n, d in frames if n == "tool_start"}
    ended = {d["id"] for n, d in frames if n == "tool_end"}
    assert started == ended, "every tool_start is closed"
    return frames[-1][1]
