"""Record storage: merge rules (T7), reads for the creator UI, follow-up status and the type lock.

One record per (agent × conversation): ``records/{agent_id}__{conversation_id}`` (SPEC §5.1).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from google.cloud import firestore
from google.cloud.firestore_v1.async_transaction import AsyncTransaction

from topskill.auth import CurrentUser
from topskill.db import Col, get_db, utcnow
from topskill.domain import AgentDoc, RecordField, RecordSchema, SaveResult, iso_z
from topskill.errors import ApiError, Codes
from topskill.records.schema import (
    EVIDENCE_LIMIT,
    SUMMARY_LIMIT,
    InvalidValue,
    normalize_value,
    value_fits,
)
from topskill.repo import get_version

log = logging.getLogger(__name__)

Source = Literal["tool", "extraction"]
FollowStatus = Literal["new", "following", "done", "invalid"]
FOLLOW_STATUSES: tuple[str, ...] = ("new", "following", "done", "invalid")
MASK = "•••"
FOLLOW_NOTE_LIMIT = 500
TX_ATTEMPTS = 10

# Versions are immutable, so their schemas can be cached for the life of the process.
_schema_cache: dict[tuple[str, int], RecordSchema | None] = {}


def record_id(agent_id: str, conversation_id: str) -> str:
    return f"{agent_id}__{conversation_id}"


def record_ref(rid: str) -> firestore.AsyncDocumentReference:
    return get_db().collection(Col.RECORDS).document(rid)


async def version_schema(agent_id: str, version_n: int) -> RecordSchema | None:
    key = (agent_id, version_n)
    if key not in _schema_cache:
        version = await get_version(agent_id, version_n)
        if version is None:
            return None
        _schema_cache[key] = version.record_schema
    return _schema_cache[key]


def has_value(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    value = entry.get("value")
    return value is not None and value != [] and value != ""


def missing_required(schema: RecordSchema, data: dict[str, Any]) -> list[str]:
    return [f.key for f in schema.fields if f.required and not has_value(data.get(f.key))]


def _scrub_summary(summary: str, schema: RecordSchema, data: dict[str, Any], visitor_name: str) -> str:
    """Belt and braces for the "no personal data in the summary" prompt rule: blank out PII values."""
    secrets_: list[str] = []
    for f in schema.fields:
        entry = data.get(f.key)
        if f.pii and has_value(entry):
            value = entry["value"]
            secrets_.extend(str(v) for v in (value if isinstance(value, list) else [value]))
    if visitor_name:
        secrets_.append(visitor_name)
    for secret in sorted({s.strip() for s in secrets_ if len(s.strip()) >= 3}, key=len, reverse=True):
        summary = re.sub(re.escape(secret), MASK, summary, flags=re.IGNORECASE)
    return summary


def _parse_fields(schema: RecordSchema, fields: Any) -> tuple[dict[str, tuple[Any, str | None]], list[str]]:
    """Model arguments → ``{key: (normalised value | None, evidence)}`` plus error messages."""
    by_key = {f.key: f for f in schema.fields}
    if fields is None:
        return {}, []
    if not isinstance(fields, dict):
        return {}, ["'fields' must be an object mapping field keys to {value, evidence}"]
    parsed: dict[str, tuple[Any, str | None]] = {}
    errors: list[str] = []
    for key, item in fields.items():
        field = by_key.get(key)
        if field is None:
            errors.append(f"unknown field '{key}' (valid keys: {', '.join(by_key)})")
            continue
        if isinstance(item, dict) and ("value" in item or "evidence" in item):
            raw_value, raw_evidence = item.get("value"), item.get("evidence")
        else:  # tolerate a bare value
            raw_value, raw_evidence = item, None
        try:
            value = normalize_value(field, raw_value)
        except InvalidValue as exc:
            errors.append(str(exc))
            continue
        evidence = str(raw_evidence).strip()[:EVIDENCE_LIMIT] if raw_evidence not in (None, "") else None
        parsed[key] = (value, evidence or None)
    return parsed, errors


def _fail(error: str) -> SaveResult:
    return SaveResult(ok=False, saved=0, completed=False, missing_required=[], error=error)


async def apply_values(
    *,
    agent: AgentDoc,
    version_n: int,
    conversation_id: str,
    visitor: CurrentUser,
    fields: dict,
    complete: bool | None,
    summary: str | None,
    source: Source,
    only_empty: bool = False,
) -> SaveResult:
    """Merge values into the conversation's record (transaction T7, SPEC §10.3 merge rules).

    Incremental: fields not sent stay unchanged; ``value: null`` clears. Any invalid input → ``ok=False``
    with a message for the model and nothing written. No record doc is created until a real value is
    written. ``complete`` marks the record completed once (later corrections are still allowed).
    ``only_empty`` (extraction) never overwrites a field that already has a value.
    """
    schema = await version_schema(agent.id, version_n)
    if schema is None:
        return _fail("Records are not enabled for this agent.")
    parsed, errors = _parse_fields(schema, fields)
    if summary is not None and not isinstance(summary, str):
        errors.append("'summary' must be text")
    if complete is not None and not isinstance(complete, bool):
        errors.append("'complete' must be true or false")
    if errors:
        return _fail("; ".join(errors))
    summary_text = (summary or "").strip()[:SUMMARY_LIMIT]
    pii_keys = {f.key for f in schema.fields if f.pii}

    db = get_db()
    rid = record_id(agent.id, conversation_id)
    ref = db.collection(Col.RECORDS).document(rid)
    stats_ref = db.collection(Col.AGENT_STATS).document(agent.id)

    @firestore.async_transactional
    async def _merge(tx: AsyncTransaction) -> SaveResult:
        snap = await ref.get(transaction=tx)
        existing = snap.to_dict() if snap.exists else None
        if existing is not None and (
            existing.get("agent_id") != agent.id or existing.get("visitor_uid") != visitor.uid
        ):
            return _fail("This record belongs to another conversation.")
        data: dict[str, Any] = dict((existing or {}).get("data") or {})
        now = utcnow()
        saved = 0
        for key, (value, evidence) in parsed.items():
            current = data.get(key)
            if only_empty and has_value(current):
                continue
            if value is None:
                if key in data:
                    del data[key]
                    saved += 1
                continue
            entry = {
                "value": value,
                "evidence": evidence,
                "updated_at": now,
                "version": version_n,
                "source": source,
                "pii": key in pii_keys,
            }
            if (
                isinstance(current, dict)
                and current.get("value") == value
                and current.get("evidence") == evidence
            ):
                continue
            data[key] = entry
            saved += 1

        if existing is None and not data:
            if complete:
                return _fail("Nothing has been recorded yet; save at least one field before completing.")
            return SaveResult(
                ok=True, saved=0, completed=False, missing_required=missing_required(schema, data), error=None
            )

        status = (existing or {}).get("status", "in_progress")
        completed_at = (existing or {}).get("completed_at")
        if complete and status != "completed":
            if source == "extraction" and missing_required(schema, data):
                pass  # extraction may complete only when every required field is filled
            else:
                status, completed_at = "completed", now
        new_summary = (existing or {}).get("summary")
        # only_empty (extraction) never replaces a summary that is already there (e.g. written by the tool).
        if summary_text and not (only_empty and new_summary):
            new_summary = _scrub_summary(summary_text, schema, data, visitor.display_name)

        changed = (
            saved > 0
            or status != (existing or {}).get("status")
            or new_summary != (existing or {}).get("summary")
        )
        if existing is None:
            tx.create(
                ref,
                {
                    "id": rid,
                    "agent_id": agent.id,
                    "owner_uid": agent.owner_uid,
                    "conversation_id": conversation_id,
                    "visitor_uid": visitor.uid,
                    "visitor_name": visitor.display_name,
                    "visitor_email": visitor.email,
                    "version": version_n,
                    "status": status,
                    "summary": new_summary,
                    "completed_at": completed_at,
                    "data": data,
                    "follow_status": "new",
                    "follow_note": "",
                    "followed_at": None,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            tx.set(
                stats_ref, {"records": firestore.Increment(1)}, merge=True
            )  # T7: last_activity_at is billing's
        elif changed:
            tx.update(
                ref,
                {
                    "data": data,
                    "version": version_n,
                    "status": status,
                    "completed_at": completed_at,
                    "summary": new_summary,
                    "updated_at": now,
                },
            )
        return SaveResult(
            ok=True,
            saved=saved,
            completed=status == "completed",
            missing_required=missing_required(schema, data),
            error=None,
        )

    return await _merge(db.transaction(max_attempts=TX_ATTEMPTS))


async def get_record_data(agent_id: str, conversation_id: str) -> dict[str, Any] | None:
    snap = await record_ref(record_id(agent_id, conversation_id)).get()
    return snap.to_dict() if snap.exists else None


# --------------------------------------------------------------------------- creator reads


def mask_email(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def _value_to_wire(entry: dict[str, Any], *, masked: bool) -> dict[str, Any]:
    return {
        "value": MASK if masked else entry.get("value"),
        "evidence": (MASK if entry.get("evidence") else None) if masked else entry.get("evidence"),
        "updated_at": iso_z(entry.get("updated_at")),
        "source": entry.get("source", "tool"),
    }


def record_has_pii(doc: dict[str, Any], schema: RecordSchema | None) -> bool:
    """True when the schema has a PII field or any stored value was written as PII."""
    if schema and any(f.pii for f in schema.fields):
        return True
    return any(isinstance(e, dict) and e.get("pii") for e in (doc.get("data") or {}).values())


def record_to_wire(doc: dict[str, Any], schema: RecordSchema | None, *, reveal_pii: bool) -> dict[str, Any]:
    """Stored record → TS ``RecordRow`` (PII values and their evidence masked unless revealed)."""
    pii_keys = {f.key for f in schema.fields if f.pii} if schema else set()
    data = {}
    for key, entry in (doc.get("data") or {}).items():
        if not isinstance(entry, dict):
            continue
        is_pii = key in pii_keys or bool(entry.get("pii"))
        data[key] = _value_to_wire(entry, masked=is_pii and not reveal_pii)
    return {
        "id": doc["id"],
        "conversation_id": doc.get("conversation_id", ""),
        "visitor_name": doc.get("visitor_name") or "",
        "visitor_email_masked": mask_email(doc.get("visitor_email")),
        "version": int(doc.get("version") or 1),
        "status": doc.get("status", "in_progress"),
        "summary": doc.get("summary"),
        "completed_at": iso_z(doc.get("completed_at")),
        "data": data,
        "follow_status": doc.get("follow_status", "new"),
        "follow_note": doc.get("follow_note") or "",
        "followed_at": iso_z(doc.get("followed_at")),
        "created_at": iso_z(doc.get("created_at")),
        "updated_at": iso_z(doc.get("updated_at")),
    }


def _record_not_found() -> ApiError:
    return ApiError(404, Codes.RECORD_NOT_FOUND, "Record not found.")


async def get_scoped_record(agent: AgentDoc, rid: str) -> dict[str, Any]:
    """Anti-IDOR (SPEC §6.1): the record must belong to ``agent`` — 404 ``record_not_found`` otherwise."""
    if not rid.startswith(f"{agent.id}__") or "/" in rid:
        raise _record_not_found()
    snap = await record_ref(rid).get()
    doc = snap.to_dict() if snap.exists else None
    if doc is None or doc.get("agent_id") != agent.id:
        raise _record_not_found()
    return doc


def records_query(agent_id: str, *, status: str | None = None, follow_status: str | None = None):
    """Filtered (unordered) query; the caller orders by ``updated_at`` (composite indexes, SPEC §5.3)."""
    query = get_db().collection(Col.RECORDS).where(filter=firestore.FieldFilter("agent_id", "==", agent_id))
    if status:
        query = query.where(filter=firestore.FieldFilter("status", "==", status))
    if follow_status:
        query = query.where(filter=firestore.FieldFilter("follow_status", "==", follow_status))
    return query


async def update_follow(
    agent: AgentDoc, rid: str, *, follow_status: str | None, follow_note: str | None
) -> dict[str, Any]:
    doc = await get_scoped_record(agent, rid)
    updates: dict[str, Any] = {}
    if follow_status is not None:
        updates["follow_status"] = follow_status
        updates["followed_at"] = utcnow()
    if follow_note is not None:
        updates["follow_note"] = follow_note.strip()[:FOLLOW_NOTE_LIMIT]
    if updates:
        await record_ref(rid).update(updates)
        doc.update(updates)
    return doc


async def batch_follow(agent: AgentDoc, ids: list[str], follow_status: str) -> int:
    """Set ``follow_status`` on the agent's records among ``ids``; foreign/missing ids are skipped."""
    prefix = f"{agent.id}__"
    scoped = list(dict.fromkeys(i for i in ids if i.startswith(prefix) and "/" not in i))
    if not scoped:
        return 0
    db = get_db()
    refs = [db.collection(Col.RECORDS).document(i) for i in scoped]
    now = utcnow()
    batch = db.batch()
    updated = 0
    async for snap in db.get_all(refs):
        if not snap.exists or (snap.to_dict() or {}).get("agent_id") != agent.id:
            continue
        batch.update(snap.reference, {"follow_status": follow_status, "followed_at": now})
        updated += 1
    if updated:
        await batch.commit()
    return updated


# --------------------------------------------------------------------------- type lock


def _locked_keys(
    old: RecordSchema | None, new: RecordSchema | None
) -> tuple[set[str], dict[str, RecordField]]:
    """→ (keys whose type changed, keys new to the schema → their new field)."""
    if new is None:
        return set(), {}
    old_types = {f.key: f.type for f in old.fields} if old else {}
    changed = {f.key for f in new.fields if f.key in old_types and old_types[f.key] != f.type}
    added = {f.key: f for f in new.fields if f.key not in old_types}
    return changed, added


async def assert_schema_change_allowed(
    agent_id: str, old: RecordSchema | None, new: RecordSchema | None
) -> None:
    """409 ``record_field_type_locked`` when a field whose type changes already has data.

    A key that is new to the schema but present in older records (a retired field being re-added) is
    locked only if a stored value does not fit the new definition. Streams ``records where agent_id ==``
    with a projection on ``data`` **in memory** (SPEC §5.3) and stops at the first hit.
    """
    changed, added = _locked_keys(old, new)
    if not changed and not added:
        return
    query = (
        get_db()
        .collection(Col.RECORDS)
        .where(filter=firestore.FieldFilter("agent_id", "==", agent_id))
        .select(["data"])
    )
    async for snap in query.stream():
        data = (snap.to_dict() or {}).get("data") or {}
        for key in changed:
            if has_value(data.get(key)):
                raise _type_locked(key)
        for key, field in added.items():
            entry = data.get(key)
            if has_value(entry) and not value_fits(field, entry["value"]):
                raise _type_locked(key)


def _type_locked(key: str) -> ApiError:
    return ApiError(
        409,
        Codes.RECORD_FIELD_TYPE_LOCKED,
        f"The field '{key}' already has data, so its type can't be changed. Add a new field instead.",
        {"key": key},
    )
