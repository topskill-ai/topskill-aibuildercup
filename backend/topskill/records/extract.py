"""Extraction fallback (SPEC §10.3, SHOULD).

After a successful web round in which the agent did not call ``save_record``, chat spawns
``extract(...)``. It asks the ``extract`` model chain for the record fields with structured output built
from the schema and merges the result with ``only_empty=True`` (never overwriting tool-written values).
Failures are logged and swallowed.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from topskill import gemini
from topskill.auth import CurrentUser
from topskill.domain import AgentDoc, AgentVersion, MessageDoc, RecordField, RecordSchema, SaveResult
from topskill.errors import current_trace_id
from topskill.records.schema import InvalidValue, normalize_value
from topskill.records.service import apply_values
from topskill.repo import list_messages

log = logging.getLogger(__name__)

TRANSCRIPT_MESSAGES = 10
MESSAGE_CHARS = 4000

SYSTEM_INSTRUCTION = (
    "You extract structured notes from a conversation between a visitor and an AI agent. "
    "Fill a field only with what the VISITOR explicitly stated (never what the agent said or assumed, never "
    "inferred values). Leave a field null when the visitor did not state it. For each filled field, quote "
    "the visitor's words as evidence. Set complete to true only if the visitor clearly finished or wants to "
    "stop. The summary is one line describing the outcome and must not contain names, emails, phone "
    "numbers or other personal data."
)


def _value_type(field: RecordField) -> Any:
    keys = tuple(o.key for o in field.options or [])
    match field.type:
        case "single_choice":
            return Literal[keys]  # type: ignore[valid-type]
        case "multi_choice":
            return list[Literal[keys]]  # type: ignore[valid-type]
        case "number":
            return float
        case "boolean":
            return bool
        case _:
            return str


def extraction_model(schema: RecordSchema) -> type[BaseModel]:
    """Pydantic model mirroring the record schema; every value optional."""
    fields: dict[str, Any] = {}
    for f in schema.fields:
        item = create_model(
            f"Field_{f.key}",
            value=(_value_type(f) | None, Field(None, description=f"{f.label}. {f.description}".strip())),
            evidence=(str | None, Field(None, description="The visitor's own words")),
        )
        fields[f.key] = (item | None, None)
    return create_model(
        "RecordExtraction",
        **fields,
        complete=(bool, False),
        summary=(str | None, Field(None, description="One line, no personal data")),
    )


def transcript_text(messages: list[MessageDoc]) -> str:
    lines = []
    for m in messages:
        if not m.text.strip():
            continue
        who = "Visitor" if m.role == "user" else "Agent"
        lines.append(f"{who}: {m.text.strip()[:MESSAGE_CHARS]}")
    return "\n\n".join(lines)


def to_fields(parsed: BaseModel, schema: RecordSchema) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for f in schema.fields:
        item = getattr(parsed, f.key, None)
        if item is None or item.value is None or item.value == []:
            continue
        try:
            normalize_value(f, item.value)
        except InvalidValue:
            continue  # e.g. out of range: drop this field, keep the rest
        out[f.key] = {"value": item.value, "evidence": item.evidence}
    return out


async def extract(
    *, agent: AgentDoc, version: AgentVersion, conversation_id: str, visitor: CurrentUser
) -> SaveResult | None:
    """Fill empty record fields from the last messages; ``None`` when skipped or failed (never raises)."""
    schema = version.record_schema
    if schema is None:
        return None
    try:
        messages = await list_messages(conversation_id, limit=TRANSCRIPT_MESSAGES)
        transcript = transcript_text(messages)
        if not transcript:
            return None
        model = extraction_model(schema)
        parsed, model_used = await gemini.generate_structured(
            gemini.model_chain("extract"),
            f"Conversation:\n\n{transcript}",
            model,
            system_instruction=SYSTEM_INSTRUCTION,
            thinking="low",
        )
        fields = to_fields(parsed, schema)
        if not fields:
            return None
        result = await apply_values(
            agent=agent,
            version_n=version.n,
            conversation_id=conversation_id,
            visitor=visitor,
            fields=fields,
            complete=bool(getattr(parsed, "complete", False)) or None,
            summary=getattr(parsed, "summary", None),
            source="extraction",
            only_empty=True,
        )
        log.info(
            "record_extracted agent=%s conversation=%s model=%s saved=%s ok=%s",
            agent.id,
            conversation_id,
            model_used,
            result.saved,
            result.ok,
        )
        return result
    except Exception as exc:
        log.warning(
            "record_extraction_failed agent=%s conversation=%s error=%s trace_id=%s",
            agent.id,
            conversation_id,
            type(exc).__name__,
            current_trace_id(),
        )
        return None
