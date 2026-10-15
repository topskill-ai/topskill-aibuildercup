"""Live smoke tests: real Vertex Gemini + Firestore emulator.

Run manually (needs ADC and the emulators):
``../deploy/scripts/emu_exec.sh <FS> <AUTH> <HUB> <LOG> -- uv run pytest -m live tests/records/test_live.py``
"""

from __future__ import annotations

import pytest
from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import google_search
from google.genai import types

from topskill import gemini
from topskill.db import Col, get_db
from topskill.domain import SaveResult
from topskill.records import extract, make_save_record_tool, prompt_section
from topskill.records.service import record_id
from topskill.repo import get_active_version
from topskill.testing.factories import make_conversation, make_user

from .helpers import make_records_agent, visa_schema

pytestmark = [pytest.mark.live, pytest.mark.emulator]


async def _record(agent_id: str, cid: str) -> dict | None:
    snap = await get_db().collection(Col.RECORDS).document(record_id(agent_id, cid)).get()
    return snap.to_dict() if snap.exists else None


@pytest.mark.parametrize("model", ["gemini-3.8-flash", "gemini-3-flash-preview"])
async def test_live_save_record_with_google_search(model: str) -> None:
    _, agent = await make_records_agent()
    visitor = await make_user("visitor", display_name="Ana Silva")
    cid = "cnv_live" + agent.id[-8:]
    saved: list[SaveResult] = []
    tool = make_save_record_tool(
        schema=visa_schema(),
        agent=agent,
        version_n=1,
        conversation_id=cid,
        visitor=visitor,
        on_saved=saved.append,
    )
    instruction = "You are a visa advisor. Use Google Search for current facts.\n\n" + await prompt_section(
        visa_schema(), agent.id, cid
    )
    llm = LlmAgent(
        name="visa_advisor",
        model=model,
        instruction=instruction,
        tools=[google_search, tool],
        generate_content_config=types.GenerateContentConfig(
            thinking_config=gemini.thinking_config(model, "low")
        ),
    )
    sessions = InMemorySessionService()
    runner = Runner(app_name="records-live", agent=llm, session_service=sessions)
    session = await sessions.create_session(app_name="records-live", user_id=visitor.uid)
    message = (
        "Hi, I'm Ana Silva from Brazil. I want a work visa and a family visa for my husband, "
        "my budget is 12000 USD and I already have a job offer. What is the H-1B registration fee?"
    )
    text: list[str] = []
    async for ev in runner.run_async(
        user_id=visitor.uid,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
        run_config=RunConfig(streaming_mode=StreamingMode.SSE, max_llm_calls=8),
    ):
        assert not ev.error_code or ev.error_code == "STOP", ev.error_message
        if ev.partial and ev.content:
            text.extend(p.text for p in ev.content.parts if p.text and not p.thought)
    assert saved and any(r.ok and r.saved for r in saved), saved
    doc = await _record(agent.id, cid)
    assert doc is not None
    data = doc["data"]
    assert data["nationality"]["value"] == "br"
    assert set(data["visa_types"]["value"]) == {"work", "family"}
    assert data["budget_usd"]["value"] == 12000
    assert data["has_offer"]["value"] is True
    assert "".join(text).strip()


async def test_live_extraction_structured_output() -> None:
    _, agent = await make_records_agent()
    visitor = await make_user("visitor")
    conv = await make_conversation(
        agent,
        visitor,
        turns=[
            ("I'm from India and I'd like to study in Canada. I don't have an offer yet.", "Happy to help!"),
            ("My budget is about 30000 USD a year.", "Thanks, that helps."),
        ],
    )
    version = await get_active_version(agent)
    result = await extract(agent=agent, version=version, conversation_id=conv.id, visitor=visitor)
    assert result is not None and result.ok and result.saved >= 3, result
    data = (await _record(agent.id, conv.id))["data"]
    assert data["nationality"]["value"] == "in"
    assert data["budget_usd"]["value"] == 30000
    assert data["has_offer"]["value"] is False
    assert all(entry["source"] == "extraction" for entry in data.values())
