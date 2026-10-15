"""Records endpoints (SPEC §6.4 "Records"). Every route is owner-only; foreign ids → 404 (SPEC §6.1)."""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from google.cloud import firestore
from pydantic import BaseModel, Field

from topskill.auth import CurrentUser, require_creator
from topskill.db import Col, get_db, utcnow
from topskill.domain import AgentDoc, RecordSchema, message_to_wire
from topskill.pagination import clamp_limit, fetch_page
from topskill.records import service
from topskill.records.csv_export import build_csv, export_filename
from topskill.records.schema import validate_schema
from topskill.repo import get_owned_agent, list_messages

log = logging.getLogger(__name__)

router = APIRouter(tags=["records"])

EXPORT_MAX_ROWS = 10_000
TRANSCRIPT_LIMIT = 200
RecordStatus = Literal["in_progress", "completed"]


async def _table_schema(agent: AgentDoc) -> RecordSchema | None:
    """Columns follow the active version (what the agent collects now); else the current config."""
    schema = await service.version_schema(agent.id, agent.active_version)
    return schema if schema is not None else agent.config.record_schema


@router.get("/agents/{agent_id}/records")
async def list_records(
    agent_id: str,
    cursor: str | None = None,
    limit: int | None = Query(None, ge=1),
    status: RecordStatus | None = None,
    follow_status: service.FollowStatus | None = None,
    reveal_pii: bool = False,
    user: CurrentUser = Depends(require_creator),
) -> dict[str, Any]:
    agent = await get_owned_agent(agent_id, user)
    schema = await _table_schema(agent)
    query = service.records_query(agent.id, status=status, follow_status=follow_status)
    items, next_cursor = await fetch_page(
        query,
        collection=get_db().collection(Col.RECORDS),
        order_field="updated_at",
        cursor=cursor,
        limit=clamp_limit(limit),
        parse=lambda snap: snap.to_dict() or {},
    )
    return {
        "schema": schema.model_dump(mode="json") if schema else None,
        "items": [service.record_to_wire(doc, schema, reveal_pii=reveal_pii) for doc in items],
        "next_cursor": next_cursor,
    }


@router.get("/agents/{agent_id}/records/export.csv")
async def export_records_csv(agent_id: str, user: CurrentUser = Depends(require_creator)) -> Response:
    agent = await get_owned_agent(agent_id, user)
    schema = await _table_schema(agent)
    query = (
        service.records_query(agent.id)
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(EXPORT_MAX_ROWS)
    )
    records = [snap.to_dict() or {} async for snap in query.stream()]
    filename = export_filename(agent.slug, utcnow())
    return Response(
        content=build_csv(schema, records),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/agents/{agent_id}/records/{record_id}")
async def get_record(
    agent_id: str,
    record_id: str,
    reveal_pii: bool = False,
    user: CurrentUser = Depends(require_creator),
) -> dict[str, Any]:
    agent = await get_owned_agent(agent_id, user)
    doc = await service.get_scoped_record(agent, record_id)
    schema = await _table_schema(agent)
    # The visitor's own words repeat any PII the record holds, so the transcript sits behind the same
    # reveal toggle as the masked values (records without PII show it straight away).
    transcript_hidden = service.record_has_pii(doc, schema) and not reveal_pii
    messages = (
        [] if transcript_hidden else await list_messages(doc["conversation_id"], limit=TRANSCRIPT_LIMIT)
    )
    return {
        **service.record_to_wire(doc, schema, reveal_pii=reveal_pii),
        "transcript": [message_to_wire(m) for m in messages],
        "transcript_hidden": transcript_hidden,
    }


class UpdateRecordRequest(BaseModel):
    follow_status: service.FollowStatus | None = None
    follow_note: str | None = Field(None, max_length=service.FOLLOW_NOTE_LIMIT)


@router.patch("/agents/{agent_id}/records/{record_id}")
async def update_record(
    agent_id: str,
    record_id: str,
    body: UpdateRecordRequest,
    user: CurrentUser = Depends(require_creator),
) -> dict[str, Any]:
    agent = await get_owned_agent(agent_id, user)
    doc = await service.update_follow(
        agent, record_id, follow_status=body.follow_status, follow_note=body.follow_note
    )
    return service.record_to_wire(doc, await _table_schema(agent), reveal_pii=False)


class BatchFollowRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    follow_status: service.FollowStatus


@router.post("/agents/{agent_id}/records/follow")
async def batch_follow_records(
    agent_id: str, body: BatchFollowRequest, user: CurrentUser = Depends(require_creator)
) -> dict[str, int]:
    agent = await get_owned_agent(agent_id, user)
    return {"updated": await service.batch_follow(agent, body.ids, body.follow_status)}


class ValidateSchemaRequest(BaseModel):
    # Parsed by records.validate_schema (not pydantic) so every problem is a 400 record_schema_invalid.
    record_schema: dict[str, Any] | None = None


@router.post("/agents/{agent_id}/record-schema/validate")
async def validate_record_schema(
    agent_id: str, body: ValidateSchemaRequest, user: CurrentUser = Depends(require_creator)
) -> dict[str, Any]:
    agent = await get_owned_agent(agent_id, user)
    normalized = validate_schema(body.record_schema)
    await service.assert_schema_change_allowed(agent.id, agent.config.record_schema, normalized)
    return {"ok": True, "record_schema": normalized.model_dump(mode="json") if normalized else None}
