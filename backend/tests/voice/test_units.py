"""Pure logic of the voice lane (no emulator, no network)."""

from __future__ import annotations

import pytest
from google.adk.tools import FunctionTool
from google.genai import types

from tests.core.helpers import settings_env
from topskill.domain import HistoryItem, ToolLineDoc
from topskill.knowledge.citations import Collector
from topskill.voice import live
from topskill.voice.protocol import StartMessage, close_code_for
from topskill.voice.rounds import Round, is_voice_turn, join_captions, new_voice_ids
from topskill.voice.router import origin_allowed
from topskill.voice.session import VOICE_SECTION, history_section

pytestmark = pytest.mark.unit


def new_round(**kw) -> Round:
    return Round(collector=Collector(), model="gemini-live-2.5-flash", **kw)


def test_voice_ids_are_prefixed_and_valid_turn_ids() -> None:
    turn_id, request_id = new_voice_ids()
    assert is_voice_turn(turn_id) and request_id.startswith("voice_")
    assert 8 <= len(turn_id) <= 64 and turn_id != request_id


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (["What documents do I need for", "Please check."], "What documents do I need for Please check."),
        (["Once", " upon a time", " in a realm"], "Once upon a time in a realm"),
        (["Hel", "lo there"], "Hello there"),
        (["  spaced  ", "", "  out "], "spaced out"),
        ([], ""),
    ],
)
def test_join_captions(parts: list[str], expected: str) -> None:
    assert join_captions(parts) == expected


def test_round_outcomes_follow_the_text_rules() -> None:
    ok = new_round()
    ok.audio_bytes, ok.model_parts = 100, ["Answer."]
    assert ok.answered and ok.outcome() == "ok"

    barged = new_round(interrupted=True)
    barged.audio_bytes, barged.model_parts = 100, ["Partial"]
    assert barged.outcome() == "aborted"

    silent_audio = new_round()
    silent_audio.audio_bytes = 100  # audio without a caption: not chargeable
    assert silent_audio.answered and silent_audio.outcome() == "failed"

    errored = new_round(error_code="agent_run_failed")
    errored.model_parts = ["Partial"]
    assert errored.outcome() == "failed"


def test_round_is_saved_only_when_the_visitor_got_something() -> None:
    assert not new_round(user_parts=["Hmm"]).worth_saving
    assert new_round(user_parts=["Hmm"], error_code="agent_run_failed").worth_saving
    with_tool = new_round()
    with_tool.tools["c1"] = ToolLineDoc(
        id="c1", tool="search_knowledge", kind="search", title="t", status="running"
    )
    assert with_tool.worth_saving
    assert [t.status for t in with_tool.tool_lines("aborted")] == ["interrupted"]
    assert [t.status for t in with_tool.tool_lines("ok")] == ["completed"]


def test_round_usage_sums_turns() -> None:
    rnd = new_round()
    rnd.add_usage(100, 10)
    rnd.add_usage(250, None)
    usage = rnd.usage()
    assert usage is not None and (usage.input_tokens, usage.output_tokens) == (350, 10)
    assert usage.model == "gemini-live-2.5-flash"
    assert new_round().user_text == ""


def test_history_section_keeps_the_newest_messages() -> None:
    history = [HistoryItem(role="user", text="a" * 50), HistoryItem(role="model", text="b" * 50)] * 3
    text = history_section(history, max_chars=130)
    assert text.startswith("## Conversation so far\n")
    assert text.count("\n") == 2  # the two newest lines fit
    assert text.endswith("You: " + "b" * 50)
    assert history_section([]) == ""


def test_voice_rules_forbid_reading_markers() -> None:
    assert "[1]" in VOICE_SECTION and "spoken" in VOICE_SECTION


def test_build_live_config() -> None:
    decl = types.FunctionDeclaration(name="search_knowledge", description="d")
    config = live.build_live_config(instruction="Hi", declarations=[decl], web_search=True, temperature=None)
    assert config.response_modalities == [types.Modality.AUDIO]
    assert config.tools is not None and config.tools[0].function_declarations == [decl]
    assert config.tools[1].google_search is not None
    assert config.temperature is None
    assert config.realtime_input_config is not None
    assert (
        config.realtime_input_config.activity_handling == types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS
    )
    bare = live.build_live_config(instruction="Hi", declarations=[], web_search=False, temperature=0.7)
    assert bare.tools is None and bare.temperature == 0.7


def test_live_plans(monkeypatch) -> None:
    assert live.live_plans() == [
        ("gemini-live-2.5-flash", "global"),
        ("gemini-live-2.5-flash-native-audio", "us-central1"),
    ]
    with settings_env(
        monkeypatch, VOICE_MODEL_FALLBACK="gemini-live-2.5-flash", VOICE_LOCATION_FALLBACK="global"
    ):
        assert live.live_plans() == [("gemini-live-2.5-flash", "global")]
    with settings_env(monkeypatch, VOICE_MODEL_FALLBACK=""):
        assert live.live_plans() == [("gemini-live-2.5-flash", "global")]


async def test_tool_bridge_runs_adk_function_tools() -> None:
    seen: list = []

    def factory(collector) -> list:
        async def search_knowledge(query: str) -> dict:
            """Search the documents."""
            seen.append((query, collector))
            return {"results": [{"ref": "[1]"}]}

        def read_skill(name: str, offset: int = 0) -> str:
            """Read a skill."""
            return f"{name}@{offset}"

        def broken() -> dict:
            """Always fails."""
            raise RuntimeError("boom")

        return [FunctionTool(search_knowledge), FunctionTool(read_skill), FunctionTool(broken)]

    bridge = live.ToolBridge(factory, Collector())
    assert [d.name for d in bridge.declarations] == ["search_knowledge", "read_skill", "broken"]
    collector = Collector()
    tools = bridge.tools_for(collector)
    result = await live.ToolBridge.call(tools, "search_knowledge", {"query": "fees", "unexpected": 1})
    assert result == {"results": [{"ref": "[1]"}]} and seen == [("fees", collector)]
    assert await live.ToolBridge.call(tools, "read_skill", {"name": "visa"}) == {"result": "visa@0"}
    assert "error" in await live.ToolBridge.call(tools, "broken", {})
    assert await live.ToolBridge.call(tools, "nope", {}) == {"error": "Unknown tool."}


def test_origin_allowed() -> None:
    assert origin_allowed(None)
    assert origin_allowed("http://localhost:5173")
    assert origin_allowed("https://top-skill--pr-12-abc.web.app")
    assert not origin_allowed("https://evil.example")
    assert not origin_allowed("https://top-skill--x.web.app.evil.example")


def test_close_codes_and_start_message() -> None:
    assert close_code_for(402) == 4402 and close_code_for(503) == 4503 and close_code_for(42) == 4500
    start = StartMessage.model_validate({"type": "start", "dev": {"uid": "u_abc"}})
    assert start.token is None and start.dev is not None and start.dev.uid == "u_abc"
    with pytest.raises(ValueError):
        StartMessage.model_validate({"type": "start", "dev": {"uid": "x"}})


def test_voice_router_is_mounted() -> None:
    from topskill.main import create_app

    app = create_app()
    assert app.state.mounted_routers[-1] == "topskill.voice.router:router"
    assert "/api/public/voice/config" in app.openapi()["paths"]


async def test_concurrent_claims_leave_exactly_one_session() -> None:
    import asyncio

    from topskill.voice import registry

    class Fake:
        def __init__(self, name: str) -> None:
            self.name = name
            self.finished = asyncio.Event()
            self.replaced = False

        def replace(self) -> None:
            self.replaced = True

            async def end() -> None:  # the router releases the slot, then sets finished
                await asyncio.sleep(0.01)
                registry.release("u1", self)
                self.finished.set()

            asyncio.get_running_loop().create_task(end())

    previous, a, b = Fake("P"), Fake("A"), Fake("B")
    await registry.claim("u1", previous)
    try:
        await asyncio.gather(registry.claim("u1", a), registry.claim("u1", b))
        assert previous.replaced
        winner = registry.get("u1")
        loser = a if winner is b else b
        assert winner in (a, b) and loser.replaced and not winner.replaced
        assert registry.active_count() == 1
    finally:
        for s in (previous, a, b):
            registry.release("u1", s)


def test_voice_session_limit_stays_inside_the_live_session_cap(monkeypatch) -> None:
    from pydantic import ValidationError

    from topskill.config import Settings

    assert Settings().voice_max_session_s == 540
    monkeypatch.setenv("VOICE_MAX_SESSION_S", "600")
    with pytest.raises(ValidationError):
        Settings()


def test_needs_refresh() -> None:
    from datetime import UTC, datetime, timedelta

    class Creds:
        def __init__(self, token, valid, expiry) -> None:
            self.token, self.valid, self.expiry = token, valid, expiry

    now = datetime.now(UTC).replace(tzinfo=None)
    assert live._needs_refresh(Creds(None, False, None))
    assert live._needs_refresh(Creds("t", True, now + timedelta(minutes=2)))
    assert not live._needs_refresh(Creds("t", True, now + timedelta(minutes=30)))
    assert not live._needs_refresh(Creds("t", True, None))
