"""Shared fixtures for records tests."""

from __future__ import annotations

from typing import Any

from topskill.auth import CurrentUser
from topskill.domain import AgentDoc, RecordSchema
from topskill.records.schema import validate_schema
from topskill.testing.factories import make_agent, make_user

VISA_SCHEMA: dict[str, Any] = {
    "fields": [
        {
            "key": "nationality",
            "label": "Nationality",
            "type": "single_choice",
            "description": "Country of citizenship",
            "options": [
                {"key": "br", "label": "Brazil"},
                {"key": "in", "label": "India"},
                {"key": "other", "label": "Other"},
            ],
            "required": True,
            "show_in_table": True,
        },
        {
            "key": "visa_types",
            "label": "Visa types",
            "type": "multi_choice",
            "options": [
                {"key": "work", "label": "Work"},
                {"key": "study", "label": "Study"},
                {"key": "family", "label": "Family"},
            ],
        },
        {"key": "budget_usd", "label": "Budget (USD)", "type": "number", "min": 0, "max": 100000},
        {"key": "has_offer", "label": "Has job offer", "type": "boolean", "required": True},
        {"key": "full_name", "label": "Full name", "type": "text", "pii": True},
        {"key": "notes", "label": "Notes", "type": "long_text"},
    ]
}


def visa_schema() -> RecordSchema:
    schema = validate_schema(VISA_SCHEMA)
    assert schema is not None
    return schema


async def make_records_agent(**overrides: Any) -> tuple[CurrentUser, AgentDoc]:
    owner = await make_user("creator")
    config = {"record_schema": VISA_SCHEMA, **overrides.pop("config", {})}
    agent = await make_agent(owner, config=config, **overrides)
    return owner, agent
