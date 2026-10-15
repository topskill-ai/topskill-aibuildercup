from __future__ import annotations

import pytest

from topskill.db import Col, get_db, new_id
from topskill.domain import SaveResult
from topskill.records import apply_values, make_save_record_tool, prompt_section
from topskill.records.service import record_id
from topskill.testing.factories import make_agent, make_user

from .helpers import make_records_agent, visa_schema

pytestmark = pytest.mark.emulator


async def _ctx():
    owner, agent = await make_records_agent()
    visitor = await make_user("visitor", display_name="Ana Silva")
    return owner, agent, visitor, new_id("cnv")


async def _apply(agent, visitor, cid, fields, **kw):
    return await apply_values(
        agent=agent,
        version_n=1,
        conversation_id=cid,
        visitor=visitor,
        fields=fields,
        complete=kw.pop("complete", None),
        summary=kw.pop("summary", None),
        source=kw.pop("source", "tool"),
        **kw,
    )


async def _doc(agent, cid) -> dict | None:
    snap = await get_db().collection(Col.RECORDS).document(record_id(agent.id, cid)).get()
    return snap.to_dict() if snap.exists else None


async def _stats(agent) -> dict:
    return (await get_db().collection(Col.AGENT_STATS).document(agent.id).get()).to_dict()


async def test_incremental_merge_clear_and_stats() -> None:
    _, agent, visitor, cid = await _ctx()
    res = await _apply(agent, visitor, cid, {"nationality": {"value": "br", "evidence": "I'm from Brazil"}})
    assert res == SaveResult(ok=True, saved=1, completed=False, missing_required=["has_offer"], error=None)
    doc = await _doc(agent, cid)
    assert doc["id"] == f"{agent.id}__{cid}" and doc["owner_uid"] == agent.owner_uid
    assert doc["visitor_uid"] == visitor.uid and doc["visitor_name"] == "Ana Silva"
    assert doc["status"] == "in_progress" and doc["follow_status"] == "new" and doc["version"] == 1
    assert doc["data"]["nationality"]["value"] == "br" and doc["data"]["nationality"]["source"] == "tool"
    assert (await _stats(agent))["records"] == 1

    res = await _apply(
        agent,
        visitor,
        cid,
        {"visa_types": {"value": ["work", "work", "study"], "evidence": "work and study"}},
    )
    assert res.ok and res.saved == 1
    doc = await _doc(agent, cid)
    assert doc["data"]["nationality"]["value"] == "br"  # untouched
    assert doc["data"]["visa_types"]["value"] == ["work", "study"]

    res = await _apply(agent, visitor, cid, {"nationality": {"value": None, "evidence": "forget it"}})
    assert res.ok and res.saved == 1 and res.missing_required == ["nationality", "has_offer"]
    assert "nationality" not in (await _doc(agent, cid))["data"]
    assert (await _stats(agent))["records"] == 1  # only on first create


async def test_invalid_input_writes_nothing() -> None:
    _, agent, visitor, cid = await _ctx()
    for fields in (
        {"nationality": {"value": "mars", "evidence": "x"}, "budget_usd": {"value": 10, "evidence": "x"}},
        {"budget_usd": {"value": 100001, "evidence": "x"}},
        {"unknown": {"value": "x", "evidence": "x"}},
        "not a dict",
    ):
        res = await _apply(agent, visitor, cid, fields)
        assert not res.ok and res.error and res.saved == 0
    assert await _doc(agent, cid) is None
    res = await _apply(agent, visitor, cid, {"nationality": {"value": "mars", "evidence": "x"}})
    assert "nationality" in res.error and "br" in res.error


async def test_no_doc_until_real_value_and_complete_rules() -> None:
    _, agent, visitor, cid = await _ctx()
    res = await _apply(agent, visitor, cid, {"full_name": {"value": None, "evidence": ""}})
    assert res.ok and res.saved == 0 and await _doc(agent, cid) is None
    res = await _apply(agent, visitor, cid, {}, complete=True, summary="done")
    assert not res.ok and "Nothing has been recorded" in res.error
    assert await _doc(agent, cid) is None

    await _apply(agent, visitor, cid, {"full_name": {"value": "Ana Silva", "evidence": "I'm Ana Silva"}})
    res = await _apply(
        agent, visitor, cid, {}, complete=True, summary="Ana Silva from Brazil wants a work visa." + "x" * 600
    )
    assert res.ok and res.completed
    doc = await _doc(agent, cid)
    first_completed = doc["completed_at"]
    assert doc["status"] == "completed" and first_completed is not None
    assert doc["summary"].startswith("••• from Brazil") and len(doc["summary"]) <= 500

    res = await _apply(
        agent, visitor, cid, {"has_offer": {"value": True, "evidence": "yes I have one"}}, complete=True
    )
    doc = await _doc(agent, cid)
    assert res.ok and res.completed and doc["completed_at"] == first_completed  # set once
    assert doc["data"]["has_offer"]["value"] is True  # later corrections allowed


async def test_only_empty_never_overwrites_and_extraction_completion() -> None:
    _, agent, visitor, cid = await _ctx()
    await _apply(agent, visitor, cid, {"nationality": {"value": "br", "evidence": "Brazil"}})
    res = await _apply(
        agent,
        visitor,
        cid,
        {"nationality": {"value": "in", "evidence": "?"}, "budget_usd": {"value": 500, "evidence": "500"}},
        source="extraction",
        only_empty=True,
        complete=True,
    )
    doc = await _doc(agent, cid)
    assert res.ok and res.saved == 1 and doc["data"]["nationality"]["value"] == "br"
    assert doc["data"]["budget_usd"]["source"] == "extraction"
    assert doc["status"] == "in_progress"  # has_offer (required) still missing
    await _apply(
        agent,
        visitor,
        cid,
        {"has_offer": {"value": False, "evidence": "no"}},
        source="extraction",
        only_empty=True,
        complete=True,
    )
    assert (await _doc(agent, cid))["status"] == "completed"


async def test_records_disabled_and_foreign_record() -> None:
    owner = await make_user("creator")
    plain = await make_agent(owner)
    visitor = await make_user("visitor")
    res = await _apply(plain, visitor, new_id("cnv"), {"x": {"value": 1}})
    assert not res.ok and "not enabled" in res.error

    _, agent, visitor, cid = await _ctx()
    await _apply(agent, visitor, cid, {"nationality": {"value": "br", "evidence": "x"}})
    intruder = await make_user("visitor")
    res = await _apply(agent, intruder, cid, {"nationality": {"value": "in", "evidence": "x"}})
    assert not res.ok and (await _doc(agent, cid))["data"]["nationality"]["value"] == "br"


async def test_tool_declaration_run_and_callback() -> None:
    _, agent, visitor, cid = await _ctx()
    seen: list[SaveResult] = []
    tool = make_save_record_tool(
        schema=visa_schema(),
        agent=agent,
        version_n=1,
        conversation_id=cid,
        visitor=visitor,
        on_saved=seen.append,
    )
    decl = tool._get_declaration()
    assert decl.name == "save_record" and decl.parameters_json_schema["required"] == ["fields"]
    out = await tool.run_async(
        args={
            "fields": {
                "nationality": {"value": "br", "evidence": "Brazil"},
                "has_offer": {"value": False, "evidence": "no"},
            }
        },
        tool_context=None,
    )
    assert out == {"ok": True, "saved": 2, "missing_required": []}
    assert seen[-1].saved == 2
    out = await tool.run_async(
        args={"fields": {"budget_usd": {"value": "lots", "evidence": "x"}}}, tool_context=None
    )
    assert out["ok"] is False and "budget_usd" in out["error"] and not seen[-1].ok
    out = await tool.run_async(
        args={"fields": {}, "complete": True, "summary": "Wants a work visa."}, tool_context=None
    )
    assert out == {"ok": True, "saved": 0, "missing_required": [], "completed": True}

    def boom(_: SaveResult) -> None:
        raise RuntimeError("callback bug")

    tool2 = make_save_record_tool(
        schema=visa_schema(), agent=agent, version_n=1, conversation_id=cid, visitor=visitor, on_saved=boom
    )
    assert (await tool2.run_async(args={"fields": {}}, tool_context=None))["ok"] is True


async def test_tool_reports_storage_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _, agent, visitor, cid = await _ctx()

    async def broken(**_: object) -> SaveResult:
        raise RuntimeError("firestore down")

    monkeypatch.setattr("topskill.records.tool.apply_values", broken)
    seen: list[SaveResult] = []
    tool = make_save_record_tool(
        schema=visa_schema(),
        agent=agent,
        version_n=1,
        conversation_id=cid,
        visitor=visitor,
        on_saved=seen.append,
    )
    out = await tool.run_async(args={"fields": {}}, tool_context=None)
    assert out["ok"] is False and "temporary problem" in out["error"] and seen[0].ok is False


async def test_prompt_section_reads_current_record() -> None:
    _, agent, visitor, cid = await _ctx()
    text = await prompt_section(visa_schema(), agent.id, cid)
    assert "Recorded so far: nothing yet." in text
    await _apply(agent, visitor, cid, {"visa_types": {"value": ["family"], "evidence": "family"}})
    text = await prompt_section(visa_schema(), agent.id, cid)
    assert "- visa_types: Family" in text and "Still missing (required): nationality, has_offer" in text
