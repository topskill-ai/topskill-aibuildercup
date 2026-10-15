from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from tests.chat.doubles import FakeRecords
from tests.voice.fakes import FakeConnector, VoiceKnowledge, visa_passage
from topskill import billing
from topskill.chat import deps as chat_deps
from topskill.voice import live
from topskill.voice.session import VoiceSession


@dataclass
class VoiceEnv:
    connector: FakeConnector
    knowledge: VoiceKnowledge
    records: FakeRecords


@pytest.fixture
def voice_env() -> Iterator[VoiceEnv]:
    """Fake Gemini Live + real billing + knowledge/records doubles, fast watchdog."""
    env = VoiceEnv(
        connector=FakeConnector(), knowledge=VoiceKnowledge([visa_passage()]), records=FakeRecords()
    )
    live.set_connector(env.connector)
    chat_deps.set_deps(chat_deps.Deps(billing=billing, knowledge=env.knowledge, records=env.records))
    old_tick = VoiceSession.TICK_S
    VoiceSession.TICK_S = 0.05
    yield env
    VoiceSession.TICK_S = old_tick
    chat_deps.set_deps(None)
    live.set_connector(None)
