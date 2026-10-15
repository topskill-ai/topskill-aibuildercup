"""Pure-logic tests of the chat lane (no emulator)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from google.adk.events import Event
from google.adk.models import FallbackModel
from google.genai import types

from tests.core.helpers import settings_env
from topskill.chat import build_instruction, build_runtime_agent, classify_outcome, get_deps, locks
from topskill.chat.agent_factory import agent_name, search_mode_for
from topskill.chat.deps import is_stub
from topskill.chat.events import TurnState, clip_target, describe_tool
from topskill.chat.history import strip_citation_markers, trim_history
from topskill.chat.skill_tools import make_skill_tools, safe_path, strip_front_matter
from topskill.db import new_id
from topskill.domain import AgentConfig, AgentDoc, AgentVersion, MessageDoc, Pricing, SkillFile, SkillRef
from topskill.errors import ApiError
from topskill.storage import get_blob_store
from topskill.testing import fake_llm
from topskill.testing.stubs import StubCollector

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)


def _agent(**config) -> AgentDoc:
    return AgentDoc(
        id="agt_x",
        owner_uid="owner",
        slug="visa-advisor-3fa9c1",
        title="Visa Advisor",
        pricing=Pricing(),
        config=AgentConfig(soul="You are a visa advisor.", skill_ids=["skl_1"], **config),
        created_at=NOW,
        updated_at=NOW,
    )


def _version(skills: list[SkillRef] | None = None, **kw) -> AgentVersion:
    data = {
        "n": 1,
        "created_at": NOW,
        "created_by": "owner",
        "title": "Visa Advisor",
        "soul": "You are a visa advisor.",
        "model": "gemini-3.8-flash",
        "temperature": None,
        "web_search_enabled": True,
        "skills": skills or [],
        "record_schema": None,
    }
    data.update(kw)
    return AgentVersion(**data)


# --------------------------------------------------------------------------- outcome


@pytest.mark.parametrize(
    ("abort", "finished", "text", "expected"),
    [
        (False, True, "answer", "ok"),
        (False, True, "", "failed"),
        (False, True, "  \n", "failed"),
        (False, False, "", "failed"),
        (True, False, "partial answer", "aborted"),
        (True, False, "", "aborted"),
        (True, True, "full answer", "ok"),  # stop after the runner finished: still a paid round
        (True, True, "", "failed"),
    ],
)
def test_classify_outcome(abort: bool, finished: bool, text: str, expected: str) -> None:
    assert classify_outcome(abort_requested=abort, runner_finished=finished, answer_text=text) == expected


# --------------------------------------------------------------------------- tool naming


def test_describe_tool_naming() -> None:
    assert describe_tool("search_knowledge", {"query": "work visa fees"}) == (
        "search",
        "Searched the knowledge base",
        "work visa fees",
    )
    assert describe_tool("google_search_agent", {"request": "fees 2026"}) == (
        "web",
        "Searched the web",
        "fees 2026",
    )
    assert describe_tool("read_skill", {"name": "Visa"}) == ("skill", "Read the playbook", "Visa")
    assert describe_tool("read_skill_file", {"name": "Visa", "path": "refs/a.md"})[2] == "Visa · refs/a.md"
    kind, title, target = describe_tool(
        "save_record", {"fields": {"a": {"value": "secret value"}, "b": {}, "c": {}}, "complete": True}
    )
    assert (kind, title, target) == ("record", "Saved notes", "3 fields · completed")
    assert "secret" not in target
    assert describe_tool("save_record", {"fields": {"a": {}}})[2] == "1 field"
    assert describe_tool("lookup_rates", {"x": 1}) == ("other", "Lookup rates", "")


def test_clip_target() -> None:
    assert clip_target("  a\n b ") == "a b"
    assert len(clip_target("x" * 200)) == 80
    assert "abc123" not in clip_target("my token=abc123 please")
    assert clip_target(None) == ""


# --------------------------------------------------------------------------- history


def _msg(role: str, text: str, i: int) -> MessageDoc:
    return MessageDoc(
        id=f"{role}{i}", role=role, text=text, status="ok", turn_id="t", request_id="r",
        created_at=NOW,
    )  # fmt: skip


def test_trim_history_rules() -> None:
    msgs = [
        _msg("model", "greeting before any user", 0),
        _msg("user", "first", 1),
        _msg("model", "", 2),  # failed reply: skipped
        _msg("user", "second " + "x" * 9000, 3),
        _msg("model", "aborted text the visitor saw", 4),
    ]
    items = trim_history(msgs)
    assert items[0].role == "user" and items[0].text == "first"
    assert len(items[1].text) == 8000
    assert items[-1].text == "aborted text the visitor saw"
    big = [_msg("user" if i % 2 == 0 else "model", "y" * 7000, i) for i in range(8)]
    trimmed = trim_history(big)
    assert sum(len(i.text) for i in trimmed) <= 24_000 and trimmed[0].role == "user"


def test_history_drops_citation_markers_from_model_text() -> None:
    text = "Apply early [1]. Fees vary [3, 4].\n- Proof of funds [2-3][5].\nSee [docs](https://x) [6](https://y)."
    assert strip_citation_markers(text) == (
        "Apply early. Fees vary.\n- Proof of funds.\nSee [docs](https://x) [6](https://y)."
    )
    assert strip_citation_markers("Steps [a] and `list[0]` stay") == "Steps [a] and `list[0]` stay"
    assert strip_citation_markers("```\nx = y[1]\n```\nDone[2].") == "```\nx = y[1]\n```\nDone."
    msgs = [_msg("user", "Ask about [1] please", 0), _msg("model", "Answer [1].", 1), _msg("model", "[2]", 2)]
    items = trim_history(msgs)
    assert [(i.role, i.text) for i in items] == [("user", "Ask about [1] please"), ("model", "Answer.")]


# --------------------------------------------------------------------------- agent factory


def test_search_modes() -> None:
    assert search_mode_for(["gemini-3.8-flash"], web_enabled=False, has_function_tools=True) == "off"
    assert search_mode_for(["gemini-3.8-flash"], web_enabled=True, has_function_tools=True) == "native"
    assert search_mode_for(["gemini-2.5-pro"], web_enabled=True, has_function_tools=False) == "native"
    assert search_mode_for(["gemini-2.5-pro"], web_enabled=True, has_function_tools=True) == "bypass"
    assert (
        search_mode_for(["gemini-3.8-flash", "gemini-2.5-pro"], web_enabled=True, has_function_tools=True)
        == "bypass"
    )
    assert agent_name("visa-advisor-3fa9c1") == "agent_visa_advisor_3fa9c1"


async def test_a2a_agent_uses_portable_thinking(monkeypatch: pytest.MonkeyPatch, doubles) -> None:
    agent, version = _agent(), _version()
    llm_agent = await build_runtime_agent(agent, version, channel="a2a")
    cfg = llm_agent.generate_content_config
    assert cfg.thinking_config.thinking_budget == 1024
    assert cfg.thinking_config.thinking_level is None
    assert cfg.temperature is None
    assert isinstance(llm_agent.model, fake_llm.FakeLlm)
    assert "You are answering another AI agent" in llm_agent.instruction(None)
    with settings_env(monkeypatch, llm_mode="live"):
        live = await build_runtime_agent(agent, _version(temperature=0.7), channel="a2a")
    assert isinstance(live.model, FallbackModel)
    assert [getattr(m, "model", m) for m in live.model.models] == [
        "gemini-3.8-flash",
        "gemini-3-flash-preview",
    ]
    assert 404 in live.model.retriable_status_codes
    assert live.generate_content_config.thinking_config.thinking_level is None
    assert live.generate_content_config.temperature == 0.7
    with pytest.raises(ValueError):
        await build_runtime_agent(agent, version, channel="web")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- prompt + skill tools


async def _skill(skill_id: str, body: str, extra: dict[str, bytes] | None = None) -> SkillRef:
    store = get_blob_store()
    prefix = f"skills/{skill_id}/"
    await store.put(prefix + "SKILL.md", body.encode(), "text/markdown")
    files = [SkillFile(path="SKILL.md", size=len(body), mime="text/markdown", is_text=True)]
    for path, data in (extra or {}).items():
        await store.put(prefix + path, data, "application/octet-stream")
        files.append(SkillFile(path=path, size=len(data), mime="text/plain", is_text=path.endswith(".md")))
    return SkillRef(
        skill_id=skill_id, slug="visa", name="Visa Playbook", description="How to plan a work visa.",
        gcs_prefix=prefix, body_chars=len(body), files=files,
    )  # fmt: skip


async def test_instruction_sections_inline() -> None:
    ref = await _skill(
        new_id("skl"),
        "---\nname: visa\n---\n# Steps\nAsk for {nationality} first.",
        {"refs/fees.md": b"Fee table", "img.png": b"\x89PNG"},
    )
    version = _version([ref])
    text = await build_instruction(
        _agent(), version, channel="web", has_knowledge=True, record_section="## Rec"
    )
    order = ["You are a visa advisor.", "## Working rules", "## Playbook: Visa Playbook", "## Reference",
             "## Web search", "## Rec"]  # fmt: skip
    positions = [text.index(s) for s in order]
    assert positions == sorted(positions)
    assert "name: visa" not in text and "{nationality}" in text and "- refs/fees.md" in text
    assert "Answering an AI agent" not in text
    a2a = await build_instruction(
        _agent(), version, channel="a2a", has_knowledge=False, record_section="## Rec"
    )
    assert "## Rec" not in a2a and "Answering an AI agent" in a2a and "Reference documents" not in a2a
    empty = await build_instruction(
        _agent(), _version(soul="", web_search_enabled=False), channel="web", has_knowledge=False,
        record_section=None,
    )  # fmt: skip
    assert empty.startswith("You are «Visa Advisor», a helpful assistant.") and "Web search" not in empty


async def test_skill_tools_paging_and_safety() -> None:
    body = "B" * 13_000
    ref = await _skill(new_id("skl"), body, {"refs/a.md": b"A" * 9000, "bin.dat": b"\x00\x01"})
    version = _version([ref])
    text = await build_instruction(_agent(), version, channel="web", has_knowledge=False, record_section=None)
    assert "## Playbooks" in text and "read_skill" in text and "B" * 100 not in text
    tools = {t.name: t for t in make_skill_tools([ref], inline=False)}
    assert set(tools) == {"read_skill", "read_skill_file"}
    page1 = await tools["read_skill"].func(name="visa playbook")
    assert len(page1["content"]) == 8000 and page1["next"] == "Next page: offset=8000"
    page2 = await tools["read_skill"].func(name="visa", offset=8000)
    assert len(page2["content"]) == 5000 and "next" not in page2
    f = await tools["read_skill_file"].func(name="visa", path="refs/a.md")
    assert len(f["content"]) == 8000 and f["next"].endswith("8000")
    for bad in ("../x", "/etc/passwd", "refs\\a.md", "refs/../../a.md", "missing.md"):
        assert "error" in await tools["read_skill_file"].func(name="visa", path=bad)
    assert (await tools["read_skill_file"].func(name="visa", path="bin.dat"))[
        "content"
    ] == "binary file, not readable"
    assert "error" in await tools["read_skill"].func(name="other")
    assert [t.name for t in make_skill_tools([ref], inline=True)] == ["read_skill_file"]
    assert safe_path("./refs//a.md") == "refs/a.md" and safe_path("c:/x") is None
    assert strip_front_matter("---\na: b\n---\nbody") == "body"


# --------------------------------------------------------------------------- event mapping


def _ev(parts: list[types.Part], *, partial: bool | None, author: str = "agent_x", **kw) -> Event:
    return Event(author=author, partial=partial, content=types.Content(role="model", parts=parts), **kw)


def test_event_mapping_text_thoughts_tools_grounding() -> None:
    st = TurnState(collector=StubCollector())
    frames = st.map_event(_ev([types.Part(text="hidden", thought=True)], partial=True))
    assert frames == []
    frames = st.map_event(_ev([types.Part(text="Hello ")], partial=True))
    frames += st.map_event(_ev([types.Part(text="world")], partial=True))
    frames += st.map_event(_ev([types.Part(text="Hello world")], partial=False))  # duplicate final ignored
    assert [(n, d.delta) for n, d in frames] == [("text_delta", "Hello "), ("text_delta", "world")]
    fc = types.FunctionCall(id="c1", name="search_knowledge", args={"query": "fees"})
    assert st.map_event(_ev([types.Part(function_call=fc)], partial=True)) == []  # announced from final
    start = st.map_event(_ev([types.Part(function_call=fc)], partial=False))
    assert [(n, d.id, d.kind) for n, d in start] == [("tool_start", "c1", "search")]
    fr = types.FunctionResponse(id="c1", name="search_knowledge", response={"results": []})
    end = st.map_event(_ev([types.Part(function_response=fr)], partial=False))
    assert [(n, d.status) for n, d in end] == [("tool_end", "completed")]
    # bypass-mode metadata arrives as a camelCase dict
    gm = {"webSearchQueries": ["visa fees"], "groundingChunks": [{"web": {"uri": "https://x/1", "title": "gov.br"}}],
          "searchEntryPoint": {"renderedContent": "<div>chips</div>"}}  # fmt: skip
    web = st.map_event(Event(author="agent_x", partial=False, grounding_metadata=gm))
    assert [(n, d.id) for n, d in web] == [("tool_start", "web-0"), ("tool_end", "web-0")]
    assert st.map_event(Event(author="agent_x", partial=True, grounding_metadata=gm)) == []  # deduped
    assert st.collector.search_entry_point_html == "<div>chips</div>"
    assert st.answer_text == "Hello world" and st.visible
    # non-streaming final response (no partial text since the last final) is used once
    frames = st.map_event(_ev([types.Part(text=" More.")], partial=False))
    assert [d.delta for _, d in frames] == [" More."]
    # error events
    st.map_event(Event(author="agent_x", error_code="SAFETY", error_message="blocked"))
    assert st.error_code == "SAFETY"


def test_event_mapping_search_agent_suppresses_synthetic_lines() -> None:
    st = TurnState(collector=StubCollector())
    fc = types.FunctionCall(id="g1", name="google_search_agent", args={"request": "visa fees"})
    st.map_event(_ev([types.Part(function_call=fc)], partial=False))
    gm = types.GroundingMetadata(web_search_queries=["visa fees", "other"])
    assert st.map_event(Event(author="agent_x", partial=False, grounding_metadata=gm)) == []
    lines = st.tool_lines()
    assert [(t.id, t.kind, t.target) for t in lines] == [("g1", "web", "visa fees")]
    closed = st.close_unfinished(interrupted=True)
    assert [(n, d.status) for n, d in closed] == [("tool_end", "error")]
    assert st.tool_lines()[0].status == "interrupted"


def test_failed_tool_response_marks_error() -> None:
    st = TurnState(collector=StubCollector())
    fc = types.FunctionCall(id="s1", name="save_record", args={"fields": {"a": {}}})
    st.map_event(_ev([types.Part(function_call=fc)], partial=False))
    fr = types.FunctionResponse(id="s1", name="save_record", response={"ok": False, "error": "bad value"})
    assert [d.status for _, d in st.map_event(_ev([types.Part(function_response=fr)], partial=False))] == [
        "error"
    ]


# --------------------------------------------------------------------------- locks + deps


def test_locks_and_abort_registry() -> None:
    locks.acquire_turn("cnv_lock_a")
    with pytest.raises(ApiError) as err:
        locks.acquire_turn("cnv_lock_a")
    assert err.value.status == 409 and err.value.code == "turn_in_progress"
    locks.release_turn("cnv_lock_a")
    locks.release_turn("cnv_lock_a")  # idempotent
    locks.acquire_turn("cnv_lock_a")
    locks.release_turn("cnv_lock_a")

    ev = locks.register_abort("cnv_ab", "turn-1234", owner_uid="u1", agent_id="agt_1")
    assert not locks.request_abort("cnv_ab", "turn-1234", uid="u2", agent_id="agt_1")
    assert not locks.request_abort("cnv_ab", "turn-1234", uid="u1", agent_id="agt_2")
    assert not locks.request_abort("cnv_ab", "other", uid="u1", agent_id="agt_1")
    assert locks.request_abort("cnv_ab", "turn-1234", uid="u1", agent_id="agt_1") and ev.is_set()
    locks.unregister_abort("cnv_ab", "turn-1234")
    assert not locks.request_abort("cnv_ab", "turn-1234", uid="u1", agent_id="agt_1")


def test_concurrency_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    with settings_env(monkeypatch, max_concurrent_turns="1"):
        locks.acquire_turn("cnv_c1")
        try:
            with pytest.raises(ApiError) as err:
                locks.acquire_turn("cnv_c2")
            assert err.value.status == 429 and err.value.code == "rate_limited"
        finally:
            locks.release_turn("cnv_c1")


def test_per_user_turn_limit() -> None:
    convs = [f"cnv_u{i}" for i in range(locks.MAX_TURNS_PER_USER + 1)]
    for c in convs[:-1]:
        locks.acquire_turn(c, "u_busy")
    try:
        with pytest.raises(ApiError) as err:
            locks.acquire_turn(convs[-1], "u_busy")
        assert err.value.status == 429 and err.value.code == "rate_limited"
        assert not locks.is_busy(convs[-1])
        locks.acquire_turn("cnv_other_user", "u_other")  # other accounts are not affected
        locks.release_turn("cnv_other_user")
        locks.release_turn(convs[0])
        locks.release_turn(convs[0])  # idempotent: frees one slot only once
        locks.acquire_turn(convs[-1], "u_busy")
        convs.append(convs.pop(0))
    finally:
        for c in convs:
            locks.release_turn(c)
    assert locks._turns_by_uid.get("u_busy") is None


def test_deps_resolve_the_real_packages() -> None:
    import importlib

    deps = get_deps()
    for name in ("billing", "knowledge", "records"):
        assert getattr(deps, name) is importlib.import_module(f"topskill.{name}")
        assert not is_stub(getattr(deps, name))
    assert get_deps() is deps  # cached
