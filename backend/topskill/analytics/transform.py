"""Firestore document → warehouse row (pure functions, SPEC §18.4).

Rows are JSON-ready dicts whose keys are exactly the table's column names (see :mod:`.tables`): timestamps
become ISO-8601 UTC strings, unknown or malformed values become ``None``. ``owner_uid`` is required: a doc
without an owner yields ``None`` and is skipped by the sync (it could never be queried anyway).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from topskill.analytics.tables import TableSpec


def ts(value: Any) -> str | None:
    """Firestore timestamp/datetime → ``2026-10-16T08:30:00.123456Z``; anything else → ``None``."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _str(value: Any, limit: int | None = None) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text[:limit] if limit else text


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _finish(spec: TableSpec, row: dict[str, Any]) -> dict[str, Any] | None:
    if not row.get("owner_uid") or not row.get("id") or not row.get("created_at"):
        return None
    return {name: row.get(name) for name in spec.column_names if name != "synced_at"}


def ledger_row(spec: TableSpec, doc_id: str, d: dict[str, Any]) -> dict[str, Any] | None:
    return _finish(
        spec,
        {
            "id": doc_id,
            "owner_uid": _str(d.get("uid")),
            "request_id": _str(d.get("request_id")),
            "kind": _str(d.get("kind")),
            "category": _str(d.get("category")),
            "amount_cents": _int(d.get("amount_cents")),
            "balance_after_cents": _int(d.get("balance_after_cents")),
            "unit_price_cents": _int(d.get("unit_price_cents")),
            "billing_mode": _str(d.get("billing_mode")),
            "counterparty_uid": _str(d.get("counterparty_uid")),
            "agent_id": _str(d.get("agent_id")),
            "agent_slug": _str(d.get("agent_slug")),
            "agent_title": _str(d.get("agent_title")),
            "conversation_id": _str(d.get("conversation_id")),
            "mandate_id": _str(d.get("mandate_id")),
            "a2a_call_id": _str(d.get("a2a_call_id")),
            "round_index": _int(d.get("round_index")),
            "model": _str(d.get("model")),
            "memo": _str(d.get("memo"), 300),
            "created_at": ts(d.get("created_at")),
        },
    )


def conversation_row(spec: TableSpec, doc_id: str, d: dict[str, Any]) -> dict[str, Any] | None:
    return _finish(
        spec,
        {
            "id": doc_id,
            "owner_uid": _str(d.get("owner_uid")),
            "agent_id": _str(d.get("agent_id")),
            "agent_slug": _str(d.get("agent_slug")),
            "visitor_uid": _str(d.get("visitor_uid")),
            "channel": _str(d.get("channel")) or "web",
            "title": _str(d.get("title"), 80),
            "rounds_ok": _int(d.get("rounds_ok")),
            "message_count": _int(d.get("message_count")),
            "created_at": ts(d.get("created_at")),
            "updated_at": ts(d.get("updated_at")),
        },
    )


def round_row(
    spec: TableSpec, conversation_id: str, conv: dict[str, Any], message_id: str, m: dict[str, Any]
) -> dict[str, Any] | None:
    """A model message of ``conversations/{conversation_id}`` → one ``rounds`` row (``None`` otherwise)."""
    if m.get("role") != "model":
        return None
    charge = m.get("charge") if isinstance(m.get("charge"), dict) else None
    usage = m.get("usage") if isinstance(m.get("usage"), dict) else {}
    citations = m.get("citations") if isinstance(m.get("citations"), list) else []
    tools = m.get("tools") if isinstance(m.get("tools"), list) else []
    kinds = [c.get("kind") for c in citations if isinstance(c, dict)]
    owner = _str(conv.get("owner_uid"))
    visitor = _str(conv.get("visitor_uid"))
    return _finish(
        spec,
        {
            "id": f"{conversation_id}__{message_id}",
            "owner_uid": owner,
            "agent_id": _str(conv.get("agent_id")),
            "agent_slug": _str(conv.get("agent_slug")),
            "conversation_id": conversation_id,
            "visitor_uid": visitor,
            "by_owner": bool(owner and visitor and owner == visitor),
            "status": _str(m.get("status")),
            "counted": charge is not None,
            "is_free": _bool(charge.get("free")) if charge else None,
            "round_index": _int(charge.get("round_index")) if charge else None,
            "charged_cents": (_int(charge.get("charged_cents")) or 0) if charge else 0,
            "model": _str(usage.get("model")),
            "input_tokens": _int(usage.get("input_tokens")),
            "output_tokens": _int(usage.get("output_tokens")),
            "tool_calls": len(tools),
            "knowledge_citations": sum(1 for k in kinds if k == "knowledge"),
            "web_citations": sum(1 for k in kinds if k == "web"),
            "error_code": _str(m.get("error_code")),
            "created_at": ts(m.get("created_at")),
        },
    )


def a2a_call_row(spec: TableSpec, doc_id: str, d: dict[str, Any]) -> dict[str, Any] | None:
    return _finish(
        spec,
        {
            "id": doc_id,
            "owner_uid": _str(d.get("owner_uid")),
            "agent_id": _str(d.get("agent_id")),
            "agent_slug": _str(d.get("agent_slug")),
            "agent_title": _str(d.get("agent_title")),
            "payer_uid": _str(d.get("payer_uid")),
            "mandate_id": _str(d.get("mandate_id")),
            "status": _str(d.get("status")),
            "price_cents": _int(d.get("price_cents")),
            "charged_cents": _int(d.get("charged_cents")),
            "creator_share_cents": _int(d.get("creator_share_cents")),
            "model": _str(d.get("model")),
            "error_code": _str(d.get("error_code")),
            "request_preview": _str(d.get("request_preview"), 120),
            "created_at": ts(d.get("created_at")),
            "finished_at": ts(d.get("finished_at")),
        },
    )


def record_row(spec: TableSpec, doc_id: str, d: dict[str, Any]) -> dict[str, Any] | None:
    data = d.get("data") if isinstance(d.get("data"), dict) else {}
    filled = sorted(
        key
        for key, entry in data.items()
        if isinstance(entry, dict) and entry.get("value") not in (None, "", [])
    )
    return _finish(
        spec,
        {
            "id": doc_id,
            "owner_uid": _str(d.get("owner_uid")),
            "agent_id": _str(d.get("agent_id")),
            "conversation_id": _str(d.get("conversation_id")),
            "status": _str(d.get("status")),
            "follow_status": _str(d.get("follow_status")),
            "fields_filled": len(filled),
            "filled_keys": filled,
            "has_summary": bool(d.get("summary")),
            "created_at": ts(d.get("created_at")),
            "updated_at": ts(d.get("updated_at")),
            "completed_at": ts(d.get("completed_at")),
            "followed_at": ts(d.get("followed_at")),
        },
    )


def agent_row(spec: TableSpec, doc_id: str, d: dict[str, Any]) -> dict[str, Any] | None:
    pricing = d.get("pricing") if isinstance(d.get("pricing"), dict) else {}
    config = d.get("config") if isinstance(d.get("config"), dict) else {}
    return _finish(
        spec,
        {
            "id": doc_id,
            "owner_uid": _str(d.get("owner_uid")),
            "slug": _str(d.get("slug")),
            "title": _str(d.get("title")),
            "status": _str(d.get("status")),
            "listed": _bool(d.get("listed")),
            "deleted": bool(d.get("deleted")),
            "pricing_mode": _str(pricing.get("mode")),
            "price_cents": _int(pricing.get("price_cents")),
            "free_rounds": _int(pricing.get("free_rounds")),
            "a2a_enabled": _bool(pricing.get("a2a_enabled")),
            "a2a_price_cents": _int(pricing.get("a2a_price_cents")),
            "model": _str(config.get("model")),
            "created_at": ts(d.get("created_at")),
            "updated_at": ts(d.get("updated_at")),
        },
    )
