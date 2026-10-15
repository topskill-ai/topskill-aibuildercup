"""The ``save_record`` tool and the records section of the system prompt (SPEC §10.3).

Live probe (2026-10-15, google-adk 2.12.0, google-genai 2.31.0, Vertex AI location ``global``; script
``/private/tmp/claude-501/topskill-probes/records/p_save_record.py``):

* A ``BaseTool`` subclass whose ``_get_declaration`` returns
  ``FunctionDeclaration(parameters_json_schema=...)`` was registered next to the built-in
  ``google_search`` tool on ``gemini-3.8-flash`` (thinking_level LOW, SSE streaming) and on the fallback
  ``gemini-3-flash-preview``. Vertex accepted the request on both models: Google Search ran (web queries
  plus grounding chunks) and ``save_record`` was called in the same turn.
* The schema had the nested ``fields`` object (one property per record field, each
  ``{value, evidence}``), a nullable single-choice enum, a nullable array of enums, a nullable number with
  ``minimum``/``maximum``, a nullable boolean and a nullable string, plus ``complete`` and ``summary``.
* All three ways of writing "nullable" were accepted and produced well-formed arguments:
  JSON-schema ``"type": [T, "null"]`` (with ``None`` appended to ``enum``), ``anyOf: [T, {"type": "null"}]``,
  and an OpenAPI ``types.Schema(nullable=True)`` passed as ``parameters``. The model sent ``"value": null``
  to clear a field when asked to forget it, arrays of enum keys for multi-choice, numbers for numbers and
  ``false`` for a boolean.
* **Decision:** the dynamic schema is used (no fallback tool needed). Nullable values are written as
  ``"type": [T, "null"]``, the first variant verified. ``apply_values`` still validates every argument
  server-side, so a malformed call gets ``{"ok": false, "error": ...}`` and writes nothing.
* Observed behaviour that shaped the prompt section: without a firm rule the model called
  ``complete=true`` in the first turn, and the fallback model put the visitor's name in the summary.
  The prompt therefore restricts ``complete`` to "everything collected or the visitor wants to stop",
  and ``apply_values`` blanks PII values out of summaries.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from google.adk.tools import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from topskill.auth import CurrentUser
from topskill.domain import AgentDoc, RecordField, RecordSchema, SaveResult
from topskill.errors import current_trace_id
from topskill.records.schema import SUMMARY_LIMIT, TEXT_LIMITS, format_range
from topskill.records.service import apply_values, get_record_data, has_value

log = logging.getLogger(__name__)

TOOL_NAME = "save_record"
TOOL_DESCRIPTION = (
    "Save facts the visitor explicitly stated into the structured record of this conversation. "
    "Send only the fields that changed, each as {value, evidence} where evidence quotes the visitor's words. "
    "Use value null to clear a field. Set complete=true (with a one-line summary without personal data) "
    "only when every field has been collected or the visitor wants to stop."
)
RECORDED_VALUE_LIMIT = 200


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    out = dict(schema)
    out["type"] = [schema["type"], "null"]
    if "enum" in out:
        out["enum"] = [*out["enum"], None]
    return out


def _value_schema(field: RecordField) -> dict[str, Any]:
    keys = [o.key for o in field.options or []]
    match field.type:
        case "single_choice":
            return _nullable({"type": "string", "enum": keys})
        case "multi_choice":
            return _nullable({"type": "array", "items": {"type": "string", "enum": keys}})
        case "number":
            schema: dict[str, Any] = {"type": "number"}
            if field.min is not None:
                schema["minimum"] = field.min
            if field.max is not None:
                schema["maximum"] = field.max
            return _nullable(schema)
        case "boolean":
            return _nullable({"type": "boolean"})
        case _:
            return _nullable({"type": "string", "maxLength": TEXT_LIMITS[field.type]})


def _field_line(field: RecordField) -> str:
    """``key (label, type, required): description. Options: k=label; Range: a–b``."""
    head = f"{field.key} ({field.label}, {field.type}{', required' if field.required else ''})"
    parts = [f"{head}: {field.description}" if field.description else head]
    if field.options:
        parts.append("Options: " + ", ".join(f"{o.key}={o.label}" for o in field.options))
    rng = format_range(field)
    if rng:
        parts.append(f"Range: {rng}")
    return ". ".join(parts)


def parameters_json_schema(schema: RecordSchema) -> dict[str, Any]:
    """JSON schema of the ``save_record`` arguments generated from the record schema (probe-verified)."""
    properties = {
        f.key: {
            "type": "object",
            "description": _field_line(f),
            "properties": {
                "value": _value_schema(f),
                "evidence": {
                    "type": "string",
                    "description": "The visitor's own words supporting the value.",
                },
            },
            "required": ["value", "evidence"],
        }
        for f in schema.fields
    }
    return {
        "type": "object",
        "properties": {
            "fields": {
                "type": "object",
                "description": "Only the fields to set or clear in this call.",
                "properties": properties,
            },
            "complete": {
                "type": "boolean",
                "description": "True only when everything is collected or the visitor wants to stop.",
            },
            "summary": {
                "type": "string",
                "maxLength": SUMMARY_LIMIT,
                "description": "One line describing the outcome, without personal data. Send with complete.",
            },
        },
        "required": ["fields"],
    }


def _result_for_model(result: SaveResult) -> dict[str, Any]:
    if not result.ok:
        return {"ok": False, "error": result.error or "The values could not be saved."}
    out: dict[str, Any] = {"ok": True, "saved": result.saved, "missing_required": result.missing_required}
    if result.completed:
        out["completed"] = True
    return out


class SaveRecordTool(BaseTool):
    """``save_record`` with a parameter schema generated from the agent's record schema (web channel)."""

    def __init__(
        self,
        *,
        schema: RecordSchema,
        agent: AgentDoc,
        version_n: int,
        conversation_id: str,
        visitor: CurrentUser,
        on_saved: Callable[[SaveResult], None],
    ) -> None:
        super().__init__(name=TOOL_NAME, description=TOOL_DESCRIPTION)
        self.schema = schema
        self.agent = agent
        self.version_n = version_n
        self.conversation_id = conversation_id
        self.visitor = visitor
        self.on_saved = on_saved

    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema=parameters_json_schema(self.schema),
        )

    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext | None) -> Any:
        try:
            result = await apply_values(
                agent=self.agent,
                version_n=self.version_n,
                conversation_id=self.conversation_id,
                visitor=self.visitor,
                fields=args.get("fields") if isinstance(args, dict) else None,
                complete=args.get("complete") if isinstance(args, dict) else None,
                summary=args.get("summary") if isinstance(args, dict) else None,
                source="tool",
            )
        except Exception as exc:
            log.error(
                "save_record_failed agent=%s conversation=%s error=%s trace_id=%s",
                self.agent.id,
                self.conversation_id,
                type(exc).__name__,
                current_trace_id(),
                exc_info=exc,
            )
            result = SaveResult(
                ok=False,
                saved=0,
                completed=False,
                missing_required=[],
                error="Saving failed because of a temporary problem. Continue the conversation without it.",
            )
        try:
            self.on_saved(result)
        except Exception:
            log.exception("save_record_callback_failed trace_id=%s", current_trace_id())
        return _result_for_model(result)


def make_save_record_tool(
    *,
    schema: RecordSchema,
    agent: AgentDoc,
    version_n: int,
    conversation_id: str,
    visitor: CurrentUser,
    on_saved: Callable[[SaveResult], None],
) -> BaseTool:
    """One tool per web turn (the chat lane registers it only when the active version has a schema)."""
    return SaveRecordTool(
        schema=schema,
        agent=agent,
        version_n=version_n,
        conversation_id=conversation_id,
        visitor=visitor,
        on_saved=on_saved,
    )


# --------------------------------------------------------------------------- prompt section

RULES = (
    "Rules for the record:",
    "- Only record what the visitor explicitly said. Never infer or guess values.",
    "- Ask at most one question per turn, woven naturally into the conversation, and only when it helps.",
    "- If the visitor declines to answer, skip that field and do not ask again.",
    "- Never offer incentives for personal data.",
    "- Never mention the tool, the field keys, a form or a record to the visitor.",
    "- When the visitor corrects something, call save_record again with the new value; use null to clear.",
    "- Call save_record with complete=true and a one-line summary without personal data only when every "
    "field is collected or the visitor wants to stop.",
)


def _display(field: RecordField | None, value: Any) -> str:
    labels = {o.key: o.label for o in (field.options or [])} if field else {}
    if isinstance(value, list):
        text = ", ".join(labels.get(v, str(v)) for v in value)
    elif isinstance(value, bool):
        text = "yes" if value else "no"
    else:
        text = labels.get(value, str(value)) if isinstance(value, str) else str(value)
    return text if len(text) <= RECORDED_VALUE_LIMIT else text[: RECORDED_VALUE_LIMIT - 1] + "…"


def build_prompt_section(schema: RecordSchema, data: dict[str, Any] | None, status: str | None) -> str:
    """Pure renderer for :func:`prompt_section` (unit-testable)."""
    data = data or {}
    lines = [
        "## Structured notes",
        "While helping the visitor, collect the following information with the save_record tool:",
        *(f"- {_field_line(f)}" for f in schema.fields),
        "",
    ]
    recorded = [f for f in schema.fields if has_value(data.get(f.key))]
    if recorded:
        lines.append("Recorded so far:")
        lines.extend(f"- {f.key}: {_display(f, data[f.key]['value'])}" for f in recorded)
    else:
        lines.append("Recorded so far: nothing yet.")
    missing_req = [f.key for f in schema.fields if f.required and not has_value(data.get(f.key))]
    missing_opt = [f.key for f in schema.fields if not f.required and not has_value(data.get(f.key))]
    lines.append("Still missing (required): " + (", ".join(missing_req) if missing_req else "none"))
    lines.append("Not yet recorded (optional): " + (", ".join(missing_opt) if missing_opt else "none"))
    if status == "completed":
        lines.append("The record is already marked complete; only save corrections the visitor makes.")
    lines.append("")
    lines.extend(RULES)
    return "\n".join(lines)


async def prompt_section(schema: RecordSchema, agent_id: str, conversation_id: str) -> str:
    """Records section of the system prompt, rebuilt every turn from the current record (one read)."""
    doc = await get_record_data(agent_id, conversation_id)
    return build_prompt_section(schema, (doc or {}).get("data"), (doc or {}).get("status"))
