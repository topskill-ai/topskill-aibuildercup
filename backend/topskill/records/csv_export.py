"""CSV export of an agent's records (SPEC §10.3).

UTF-8 with BOM and a header row. Fixed columns, then one column per schema field label, then keys found in
the data but no longer in the schema as ``"<key> (retired)"``. Cells that could be read as a spreadsheet
formula are prefixed with ``'``. PII is exported (it is the creator's own data); the UI warns first.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any

from topskill.domain import RecordField, RecordSchema
from topskill.records.service import mask_email

FIXED_COLUMNS = (
    "Started (UTC)",
    "Completed (UTC)",
    "Status",
    "Visitor name",
    "Visitor email (masked)",
    "Summary",
    "Version",
    "Follow status",
    "Follow note",
    "Last followed (UTC)",
)
STATUS_LABELS = {"in_progress": "In progress", "completed": "Completed"}
FOLLOW_LABELS = {"new": "New", "following": "Following", "done": "Done", "invalid": "Invalid"}
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
BOM = "﻿"


def guard(cell: str) -> str:
    """Formula-injection guard: prefix ``'`` to text starting with ``= + - @ \\t \\r``."""
    return "'" + cell if cell.startswith(FORMULA_PREFIXES) else cell


def fmt_time(value: Any) -> str:
    if not isinstance(value, datetime):
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_number(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def format_value(field: RecordField | None, value: Any) -> tuple[str, bool]:
    """→ (cell text, is_numeric). Choices use option labels; multi-choice joined by ``"; "``."""
    if value is None:
        return "", False
    labels = {o.key: o.label for o in (field.options or [])} if field else {}
    if isinstance(value, list):
        return "; ".join(labels.get(v, str(v)) for v in value), False
    if isinstance(value, bool):
        return ("Yes" if value else "No"), False
    if isinstance(value, int | float):
        return _fmt_number(value), True
    text = str(value)
    return labels.get(text, text), False


def retired_keys(schema: RecordSchema | None, records: list[dict[str, Any]]) -> list[str]:
    known = {f.key for f in schema.fields} if schema else set()
    found: set[str] = set()
    for rec in records:
        for key, entry in (rec.get("data") or {}).items():
            if key not in known and isinstance(entry, dict) and entry.get("value") is not None:
                found.add(key)
    return sorted(found)


def build_csv(schema: RecordSchema | None, records: list[dict[str, Any]]) -> bytes:
    fields = list(schema.fields) if schema else []
    retired = retired_keys(schema, records)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    header = [*FIXED_COLUMNS, *(f.label for f in fields), *(f"{k} (retired)" for k in retired)]
    writer.writerow([guard(h) for h in header])
    for rec in records:
        data = rec.get("data") or {}
        fixed = [
            fmt_time(rec.get("created_at")),
            fmt_time(rec.get("completed_at")),
            STATUS_LABELS.get(rec.get("status", ""), rec.get("status", "")),
            rec.get("visitor_name") or "",
            mask_email(rec.get("visitor_email")),
            rec.get("summary") or "",
            str(rec.get("version") or ""),
            FOLLOW_LABELS.get(rec.get("follow_status", ""), rec.get("follow_status", "")),
            rec.get("follow_note") or "",
            fmt_time(rec.get("followed_at")),
        ]
        row = [guard(c) for c in fixed]
        for field, key in [*((f, f.key) for f in fields), *((None, k) for k in retired)]:
            entry = data.get(key)
            text, numeric = format_value(field, entry.get("value") if isinstance(entry, dict) else None)
            row.append(text if numeric else guard(text))
        writer.writerow(row)
    return (BOM + out.getvalue()).encode("utf-8")


def export_filename(slug: str, now: datetime) -> str:
    return f"{slug}-records-{now.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}.csv"
