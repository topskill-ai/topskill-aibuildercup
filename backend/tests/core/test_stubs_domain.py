from __future__ import annotations

from datetime import UTC, datetime

import pytest
from google.adk.tools import BaseTool
from google.cloud import firestore
from google.genai import types

from topskill.db import Col, get_db, new_id, utcnow
from topskill.domain import (
    AgentConfig,
    Citation,
    ConversationDoc,
    MessageCharge,
    MessageDoc,
    Passage,
    RecordSchema,
    RoundCharge,
    ToolLineDoc,
    VerifiedMandate,
    message_to_wire,
)
from topskill.events import AssistantDoneData, MandateCounters
from topskill.testing.factories import make_agent, make_user
from topskill.testing.stubs import StubBilling, StubCollector, StubKnowledge, StubRecords


@pytest.mark.unit
def test_message_to_wire() -> None:
    doc = MessageDoc(
        id="m_turn_1",
        role="model",
        text="Answer [1]",
        status="ok",
        turn_id="t1",
        request_id="turn_1",
        citations=[
            Citation(
                n=1,
                kind="knowledge",
                title="visa_guide.pdf",
                file_id="kf_1",
                page_start=2,
                page_end=2,
                snippet="...",
            )
        ],
        tools=[
            ToolLineDoc(
                id="c1",
                tool="search_knowledge",
                kind="search",
                title="Searched the knowledge base",
                target="fees",
                status="completed",
            )
        ],
        charge=MessageCharge(charged_cents=100, free=False, round_index=3),
        error_code="x",
        created_at=datetime(2026, 10, 16, 8, 30, tzinfo=UTC),
    )
    wire = message_to_wire(doc)
    assert set(wire) == {
        "id",
        "role",
        "text",
        "status",
        "turn_id",
        "citations",
        "search_entry_point_html",
        "tools",
        "charge",
        "created_at",
    }
    assert wire["created_at"] == "2026-10-16T08:30:00Z"
    assert wire["charge"] == {"charged_cents": 100, "free": False, "round_index": 3}
    assert wire["citations"][0]["uri"] is None and wire["citations"][0]["page_start"] == 2
    assert wire["tools"][0]["status"] == "completed"


@pytest.mark.unit
def test_domain_defaults() -> None:
    cfg = AgentConfig(soul="You are…", skill_ids=["skl_1"])
    assert cfg.model == "gemini-3.8-flash" and cfg.temperature is None and cfg.record_schema is None
    with pytest.raises(ValueError):
        RecordSchema(fields=[])
    assert len(new_id("agt")) == 24 and new_id("agt").startswith("agt_")
    done = AssistantDoneData(
        conversation_id=None,
        message_id=None,
        turn_id="t",
        outcome="ok",
        usage=None,
        mandate=MandateCounters(id="mdt_1", spent_cents=400, budget_cents=2000, calls=2),
    )
    assert done.model_dump()["billing"] is None and done.model_dump()["mandate"]["calls"] == 2


@pytest.mark.emulator
async def test_stub_settle_round_runs_writer_in_transaction() -> None:
    owner = await make_user("creator")
    visitor = await make_user()
    agent = await make_agent(owner)
    billing = StubBilling()
    quote = await billing.preflight_round(agent, visitor)
    assert quote.free and quote.charge_cents == 0 and not quote.is_owner
    cid = new_id("cnv")
    db = get_db()
    conv_ref = db.collection(Col.CONVERSATIONS).document(cid)
    calls: list[tuple[RoundCharge, bool]] = []

    def writer(tx, charge: RoundCharge, exists: bool) -> None:
        calls.append((charge, exists))
        now = utcnow()
        if not exists:
            tx.create(
                conv_ref,
                ConversationDoc(
                    id=cid,
                    agent_id=agent.id,
                    agent_slug=agent.slug,
                    owner_uid=owner.uid,
                    visitor_uid=visitor.uid,
                    title="Hi",
                    rounds_ok=1,
                    message_count=2,
                    created_at=now,
                    updated_at=now,
                ).model_dump(mode="python"),
            )
        else:
            tx.update(
                conv_ref, {"rounds_ok": firestore.Increment(1), "message_count": firestore.Increment(2)}
            )

    first = await billing.settle_round(
        agent=agent,
        payer=visitor,
        conversation_id=cid,
        request_id="turn_a",
        succeeded=True,
        model="m",
        write_messages=writer,
    )
    assert first.outcome == "free" and first.charged_cents == 0
    failed = await billing.settle_round(
        agent=agent,
        payer=visitor,
        conversation_id=cid,
        request_id="turn_b",
        succeeded=False,
        model=None,
        write_messages=writer,
    )
    assert failed.outcome == "not_counted"
    assert [exists for _, exists in calls] == [False, True]
    conv = (await conv_ref.get()).to_dict()
    assert conv["rounds_ok"] == 2 and conv["message_count"] == 4

    mandate = VerifiedMandate(
        mandate_id="mdt_1",
        payer_uid=visitor.uid,
        audience="*",
        intent="x",
        max_amount_cents=500,
        budget_cents=2000,
        expires_at=utcnow(),
        kid="dev-1",
        payer_agent=None,
    )
    assert await billing.preflight_a2a(agent, mandate) == agent.pricing.a2a_price_cents
    charge = await billing.settle_a2a_call(call_id="c1", succeeded=True, model=None, answer_preview="")
    assert charge.status == "completed" and charge.charged_cents == 0
    status = await billing.billing_status(agent, visitor)
    assert status["can_afford_next_round"] is True and status["owner_available"] is True


@pytest.mark.unit
async def test_stub_knowledge_and_collector() -> None:
    knowledge = StubKnowledge()
    assert await knowledge.search("agt_x", "q") == [] and not await knowledge.has_ready_files("agt_x")
    tool = knowledge.make_search_tool("agt_x", None)
    assert isinstance(tool, BaseTool) and tool.name == "search_knowledge"

    collector = knowledge.new_collector()
    p1 = Passage(file_id="kf_1", filename="a.pdf", text="alpha", page_start=1, page_end=1, score=0.1)
    p2 = Passage(file_id="kf_1", filename="a.pdf", text="beta", page_start=2, page_end=2, score=0.2)
    assert collector.add_passages([p1, p2]) == [1, 2]
    assert collector.add_passages([p2, p1]) == [2, 1]
    gm = types.GroundingMetadata(
        web_search_queries=["brazil work visa"],
        grounding_chunks=[
            types.GroundingChunk(
                web=types.GroundingChunkWeb(uri="https://r/1", title="gov.br", domain="gov.br")
            )
        ],
        search_entry_point=types.SearchEntryPoint(rendered_content="<div>chips</div>"),
    )
    assert collector.add_grounding(gm) == ["brazil work visa"]
    assert collector.add_grounding(gm) == []
    assert collector.add_grounding(None) == []
    sources = collector.sources()
    assert [(c.n, c.kind) for c in sources] == [(1, "knowledge"), (2, "knowledge"), (3, "web")]
    assert collector.search_entry_point_html == "<div>chips</div>"
    assert collector.web_search_queries == ["brazil work visa"]
    assert isinstance(StubCollector(), StubCollector)


@pytest.mark.unit
async def test_stub_records() -> None:
    records = StubRecords()
    assert records.validate_schema(None) is None
    assert records.validate_schema({"fields": []}) is None
    schema = records.validate_schema(
        {"fields": [{"key": "nationality", "label": "Nationality", "type": "text"}]}
    )
    assert isinstance(schema, RecordSchema) and schema.fields[0].key == "nationality"
    assert await records.prompt_section(schema, "agt", "cnv") == ""
    assert records.is_stub and StubBilling.is_stub and StubKnowledge.is_stub
