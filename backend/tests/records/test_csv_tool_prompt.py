from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

import pytest

from topskill.records.csv_export import FIXED_COLUMNS, build_csv, export_filename, guard
from topskill.records.service import mask_email, record_to_wire
from topskill.records.tool import build_prompt_section, parameters_json_schema

from .helpers import visa_schema

pytestmark = pytest.mark.unit

T0 = datetime(2026, 10, 16, 8, 30, 5, tzinfo=UTC)


def _entry(value, evidence="said so", pii=False):
    return {
        "value": value,
        "evidence": evidence,
        "updated_at": T0,
        "version": 1,
        "source": "tool",
        "pii": pii,
    }


def _record(**data):
    return {
        "id": "agt_1__cnv_1",
        "conversation_id": "cnv_1",
        "visitor_name": "Ana Silva",
        "visitor_email": "ana@example.com",
        "version": 2,
        "status": "completed",
        "summary": "=HYPERLINK(evil)",
        "completed_at": T0,
        "data": data,
        "follow_status": "following",
        "follow_note": "+call back",
        "followed_at": None,
        "created_at": T0,
        "updated_at": T0,
    }


def test_csv_bom_columns_values_guard_and_retired() -> None:
    rec = _record(
        nationality=_entry("br"),
        visa_types=_entry(["work", "family"]),
        budget_usd=_entry(-5),
        has_offer=_entry(False),
        full_name=_entry("@Ana", pii=True),
        old_key=_entry("legacy"),
    )
    raw = build_csv(visa_schema(), [rec])
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    header, row = rows
    labels = [f.label for f in visa_schema().fields]
    assert header == [*FIXED_COLUMNS, *labels, "old_key (retired)"]
    cell = dict(zip(header, row, strict=True))
    assert cell["Started (UTC)"] == "2026-10-16 08:30:05"
    assert cell["Status"] == "Completed" and cell["Follow status"] == "Following"
    assert cell["Visitor email (masked)"] == "a***@example.com"
    assert cell["Summary"] == "'=HYPERLINK(evil)" and cell["Follow note"] == "'+call back"
    assert cell["Nationality"] == "Brazil" and cell["Visa types"] == "Work; Family"
    assert cell["Budget (USD)"] == "-5"  # numbers are not text cells
    assert cell["Has job offer"] == "No" and cell["Full name"] == "'@Ana"  # PII exported
    assert cell["Notes"] == "" and cell["old_key (retired)"] == "legacy"


def test_csv_without_schema_and_guard_chars() -> None:
    raw = build_csv(None, [_record(nationality=_entry("br"))]).decode("utf-8-sig")
    assert "nationality (retired)" in raw.splitlines()[0]
    for ch in "=+-@\t\r":
        assert guard(ch + "x") == "'" + ch + "x"
    assert guard("ok") == "ok"
    assert export_filename("visa-advisor", T0) == "visa-advisor-records-20261016T083005Z.csv"


def test_record_to_wire_masks_pii_and_email() -> None:
    rec = _record(
        full_name=_entry("Ana", "I'm Ana", pii=False), nationality=_entry("br"), retired=_entry("x", pii=True)
    )
    wire = record_to_wire(rec, visa_schema(), reveal_pii=False)
    assert wire["data"]["full_name"]["value"] == "•••" and wire["data"]["full_name"]["evidence"] == "•••"
    assert wire["data"]["retired"]["value"] == "•••"  # stored pii flag survives schema changes
    assert wire["data"]["nationality"] == {
        "value": "br",
        "evidence": "said so",
        "updated_at": "2026-10-16T08:30:05Z",
        "source": "tool",
    }
    assert wire["visitor_email_masked"] == "a***@example.com" and "visitor_email" not in wire
    assert wire["completed_at"] == "2026-10-16T08:30:05Z" and wire["followed_at"] is None
    revealed = record_to_wire(rec, visa_schema(), reveal_pii=True)
    assert revealed["data"]["full_name"]["value"] == "Ana"
    assert mask_email(None) == "" and mask_email("x@y.z") == "x***@y.z"


def test_parameters_json_schema_shape() -> None:
    js = parameters_json_schema(visa_schema())
    assert js["required"] == ["fields"] and set(js["properties"]) == {"fields", "complete", "summary"}
    props = js["properties"]["fields"]["properties"]
    assert props["nationality"]["properties"]["value"] == {
        "type": ["string", "null"],
        "enum": ["br", "in", "other", None],
    }
    assert props["visa_types"]["properties"]["value"] == {
        "type": ["array", "null"],
        "items": {"type": "string", "enum": ["work", "study", "family"]},
    }
    assert props["budget_usd"]["properties"]["value"] == {
        "type": ["number", "null"],
        "minimum": 0,
        "maximum": 100000,
    }
    assert props["has_offer"]["properties"]["value"]["type"] == ["boolean", "null"]
    assert props["full_name"]["properties"]["value"]["type"] == ["string", "null"]
    assert "Options: br=Brazil" in props["nationality"]["description"]
    assert "required" in props["nationality"]["description"]


def test_prompt_section_lists_fields_progress_and_rules() -> None:
    data = {"nationality": _entry("br"), "full_name": _entry("x" * 300), "budget_usd": {"value": None}}
    text = build_prompt_section(visa_schema(), data, "in_progress")
    assert (
        "nationality (Nationality, single_choice, required): Country of citizenship. Options: br=Brazil"
        in text
    )
    assert "budget_usd (Budget (USD), number). Range: 0–100000" in text
    assert "- nationality: Brazil" in text
    assert "- full_name: " + "x" * 199 + "…" in text
    assert "Still missing (required): has_offer" in text
    assert "Not yet recorded (optional): visa_types, budget_usd, notes" in text
    assert "Only record what the visitor explicitly said" in text
    assert "complete=true" in text and "never mention the tool" in text.lower()
    empty = build_prompt_section(visa_schema(), None, None)
    assert "Recorded so far: nothing yet." in empty
    assert "already marked complete" in build_prompt_section(visa_schema(), data, "completed")
