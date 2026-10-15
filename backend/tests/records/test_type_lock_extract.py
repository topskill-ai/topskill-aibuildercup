from __future__ import annotations

import pytest

from topskill import gemini
from topskill.db import Col, get_db, new_id
from topskill.errors import ApiError
from topskill.records import apply_values, assert_schema_change_allowed, extract, validate_schema
from topskill.records.extract import extraction_model, to_fields
from topskill.records.service import record_id
from topskill.repo import get_active_version
from topskill.testing.factories import make_conversation, make_user

from .helpers import VISA_SCHEMA, make_records_agent, visa_schema

pytestmark = pytest.mark.emulator


def _schema(*fields):
    return validate_schema({"fields": list(fields)})


async def test_type_lock_rules() -> None:
    _, agent = await make_records_agent()
    visitor = await make_user("visitor")
    old = visa_schema()
    budget_as_text = _schema({**VISA_SCHEMA["fields"][2], "type": "text"})
    await assert_schema_change_allowed(agent.id, old, budget_as_text)  # no data yet
    await assert_schema_change_allowed(agent.id, old, None)
    await apply_values(
        agent=agent,
        version_n=1,
        conversation_id=new_id("cnv"),
        visitor=visitor,
        fields={"budget_usd": {"value": 900, "evidence": "900"}},
        complete=None,
        summary=None,
        source="tool",
    )
    with pytest.raises(ApiError) as exc:
        await assert_schema_change_allowed(agent.id, old, budget_as_text)
    assert exc.value.status == 409 and exc.value.code == "record_field_type_locked"
    # Same type, new label/range: fine. Other agents' data never locks this agent.
    await assert_schema_change_allowed(
        agent.id, old, _schema({**VISA_SCHEMA["fields"][2], "label": "Budget"})
    )
    _, other = await make_records_agent()
    await assert_schema_change_allowed(other.id, old, budget_as_text)
    # Re-adding a retired key: locked only when stored values do not fit the new definition.
    without_budget = _schema(VISA_SCHEMA["fields"][0])
    await assert_schema_change_allowed(agent.id, without_budget, _schema(VISA_SCHEMA["fields"][2]))
    with pytest.raises(ApiError):
        await assert_schema_change_allowed(agent.id, without_budget, budget_as_text)


def test_extraction_model_shape() -> None:
    model = extraction_model(visa_schema())
    parsed = model.model_validate(
        {
            "nationality": {"value": "br", "evidence": "Brazil"},
            "visa_types": {"value": ["work"], "evidence": "work"},
            "budget_usd": {"value": 1e9, "evidence": "a billion"},  # out of range → dropped
            "has_offer": None,
            "complete": False,
        }
    )
    assert to_fields(parsed, visa_schema()) == {
        "nationality": {"value": "br", "evidence": "Brazil"},
        "visa_types": {"value": ["work"], "evidence": "work"},
    }
    with pytest.raises(ValueError):
        model.model_validate({"nationality": {"value": "mars"}})


async def test_extract_fills_only_empty_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _, agent = await make_records_agent()
    visitor = await make_user("visitor")
    conv = await make_conversation(agent, visitor, turns=[("I'm Brazilian and want a work visa", "Great!")])
    version = await get_active_version(agent)
    await apply_values(
        agent=agent,
        version_n=1,
        conversation_id=conv.id,
        visitor=visitor,
        fields={"nationality": {"value": "in", "evidence": "tool said"}},
        complete=None,
        summary=None,
        source="tool",
    )
    captured = {}

    async def fake_structured(chain, contents, schema, **kw):
        captured["contents"] = contents
        captured["chain"] = chain
        return (
            schema.model_validate(
                {
                    "nationality": {"value": "br", "evidence": "Brazilian"},
                    "visa_types": {"value": ["work"], "evidence": "work visa"},
                }
            ),
            chain[0],
        )

    monkeypatch.setattr(gemini, "generate_structured", fake_structured)
    res = await extract(agent=agent, version=version, conversation_id=conv.id, visitor=visitor)
    assert res is not None and res.ok and res.saved == 1
    assert "Visitor: I'm Brazilian" in captured["contents"] and captured["chain"] == gemini.model_chain(
        "extract"
    )
    doc = (await get_db().collection(Col.RECORDS).document(record_id(agent.id, conv.id)).get()).to_dict()
    assert doc["data"]["nationality"]["value"] == "in"  # tool value kept
    assert doc["data"]["visa_types"]["source"] == "extraction"


async def test_extract_swallows_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    _, agent = await make_records_agent()
    visitor = await make_user("visitor")
    conv = await make_conversation(agent, visitor)
    version = await get_active_version(agent)

    async def broken(*a, **k):
        raise ApiError(502, "upstream_error", "busy")

    monkeypatch.setattr(gemini, "generate_structured", broken)
    assert await extract(agent=agent, version=version, conversation_id=conv.id, visitor=visitor) is None
    no_schema = version.model_copy(update={"record_schema": None})
    assert await extract(agent=agent, version=no_schema, conversation_id=conv.id, visitor=visitor) is None
