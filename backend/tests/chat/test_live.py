"""Live smoke tests against Vertex AI (``-m live``; never in CI). Findings are recorded in
``topskill/chat/runtime.py``'s module docstring (D6 re-verification on gemini-3.8-flash)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

import pytest
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from tests.chat.conftest import assert_protocol, parse_sse
from tests.conftest import dev_headers
from tests.core.helpers import settings_env
from topskill.chat import build_llm_agent, build_runtime_agent
from topskill.chat.agent_factory import live_model
from topskill.domain import AgentConfig, AgentDoc, AgentVersion, Passage, Pricing
from topskill.gemini import thinking_config
from topskill.testing.factories import make_agent, make_user

pytestmark = pytest.mark.live

MODEL = "gemini-3.8-flash"
NOW = datetime(2026, 10, 15, tzinfo=UTC)


def _agent_and_version() -> tuple[AgentDoc, AgentVersion]:
    agent = AgentDoc(
        id="agt_live", owner_uid="o", slug="study-advisor", title="Study Advisor", pricing=Pricing(),
        config=AgentConfig(soul="You are a study-abroad advisor.", skill_ids=["skl_1"]),
        created_at=NOW, updated_at=NOW,
    )  # fmt: skip
    version = AgentVersion(
        n=1, created_at=NOW, created_by="o", title="Study Advisor",
        soul="You are a study-abroad advisor. Use Google Search for current facts. When the visitor gives "
             "their name and budget, call save_record.",
        model=MODEL, temperature=None, web_search_enabled=True, skills=[], record_schema=None,
    )  # fmt: skip
    return agent, version


async def _run(llm_agent, question: str) -> tuple[str, list, list, int]:
    ss = InMemorySessionService()
    session = await ss.create_session(app_name="live", user_id="u1")
    runner = Runner(app_name="live", agent=llm_agent, session_service=ss)
    text, calls, grounding, thoughts = "", [], [], 0
    async for ev in runner.run_async(
        user_id="u1",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=question)]),
        run_config=RunConfig(streaming_mode=StreamingMode.SSE, max_llm_calls=8),
    ):
        assert not ev.error_code or ev.error_code == "STOP", (ev.error_code, ev.error_message)
        for part in (ev.content.parts if ev.content else None) or []:
            thoughts += bool(part.thought)
            if ev.partial and part.text and not part.thought:
                text += part.text
        if not ev.partial:
            calls += [fc.name for fc in ev.get_function_calls()]
        if ev.grounding_metadata is not None:
            grounding.append(ev.grounding_metadata)
    return text, calls, grounding, thoughts


async def test_live_native_search_with_function_tool_and_thinking_level() -> None:
    saved: list[tuple[str, int]] = []

    def save_record(customer_name: str, budget_usd: int) -> dict:
        """Save the visitor's name and budget once both are known."""
        saved.append((customer_name, budget_usd))
        return {"ok": True, "saved": 2}

    agent, version = _agent_and_version()
    llm_agent = build_llm_agent(
        agent, version, model=live_model(MODEL), instruction=version.soul, tools=[save_record],
        search_mode="native", temperature=None, thinking=thinking_config(MODEL, "low"),
    )  # fmt: skip
    assert llm_agent.generate_content_config.thinking_config.thinking_level is not None
    text, calls, grounding, thoughts = await _run(
        llm_agent,
        "My name is Lin, my budget is 40000 USD per year. What is the current tuition at the University of "
        "Toronto for international undergraduates?",
    )
    assert text.strip() and thoughts == 0
    assert "save_record" in calls and saved
    assert grounding, "google_search grounding metadata expected next to the function tool"


async def test_live_a2a_agent_portable_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, version = _agent_and_version()
    with settings_env(monkeypatch, llm_mode="live"):
        llm_agent = await build_runtime_agent(agent, version, channel="a2a")
        text, _, _, thoughts = await _run(llm_agent, "In one sentence: what is a student visa?")
    assert llm_agent.generate_content_config.thinking_config.thinking_budget == 1024
    assert text.strip() and thoughts == 0


@pytest.mark.emulator
async def test_live_web_turn_end_to_end(client, doubles, monkeypatch: pytest.MonkeyPatch) -> None:
    _, knowledge, records = doubles
    knowledge.passages = [
        Passage(file_id="kf_live", filename="visa_guide.pdf", page_start=2, page_end=2, score=0.1,
                text="Brazil: the temporary work visa (VITEM V) application fee is 290 USD and processing "
                     "takes 15 business days.")
    ]  # fmt: skip
    owner = await make_user("creator")
    visitor = await make_user()
    agent = await make_agent(
        owner,
        config={
            "soul": "You are a visa advisor for companies hiring abroad. Always search the knowledge base "
            "first and cite it as [n]. Record what the visitor tells you about themselves.",
            "model": MODEL,
            "record_schema": {"fields": [{"key": "nationality", "label": "Nationality", "type": "text"}]},
        },
    )
    with settings_env(monkeypatch, llm_mode="live"):
        resp = await client.post(
            f"/api/public/agents/{agent.slug}/chat",
            json={
                "conversation_id": None,
                "turn_id": "live" + secrets.token_hex(6),
                "message": "I'm from Brazil. What is the fee for a Brazilian work visa according to your "
                "documents, and what is today's USD to BRL exchange rate?",
            },
            headers=dev_headers(visitor.uid),
        )
    assert resp.status_code == 200, resp.text
    frames = parse_sse(resp.text)
    done = assert_protocol(frames)
    kinds = {d["kind"] for n, d in frames if n == "tool_start"}
    answer = "".join(d["delta"] for n, d in frames if n == "text_delta")
    print("LIVE kinds", kinds, "answer", answer[:300], "done", done)
    assert done["outcome"] == "ok" and done["billing"]["charged_cents"] == 100
    assert "search" in kinds and knowledge.queries
    assert "web" in kinds
    assert "[1]" in answer
    assert records.calls.saved, "save_record expected for the stated nationality"
