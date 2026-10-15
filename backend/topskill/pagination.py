"""Opaque cursors for ``?cursor=&limit=`` list endpoints (SPEC §6.1).

A cursor encodes ``(doc_id, ordering timestamp)`` of the last item of a page. :func:`fetch_page` runs a
query ordered by one timestamp field (descending by default) with the document id as tie-breaker, so the
composite indexes of SPEC §5.3 are used as declared (Firestore orders by ``__name__`` implicitly in the
direction of the last ``order_by``).
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.async_collection import AsyncCollectionReference
from google.cloud.firestore_v1.async_query import AsyncQuery
from google.cloud.firestore_v1.base_document import DocumentSnapshot
from google.cloud.firestore_v1.field_path import FieldPath

from topskill.errors import ApiError, Codes

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def _invalid_cursor() -> ApiError:
    return ApiError(
        400,
        Codes.VALIDATION_ERROR,
        "The cursor is invalid.",
        {"errors": [{"loc": ["query", "cursor"], "msg": "invalid cursor"}]},
    )


def encode_cursor(doc_id: str, ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    raw = json.dumps({"id": doc_id, "ts": ts.astimezone(UTC).isoformat()}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, datetime]:
    """→ ``(doc_id, ts)``; 400 ``validation_error`` for anything malformed."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        doc_id, ts_raw = data["id"], data["ts"]
        if not isinstance(doc_id, str) or not doc_id or "/" in doc_id or not isinstance(ts_raw, str):
            raise ValueError("bad cursor fields")
        ts = datetime.fromisoformat(ts_raw)
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid_cursor() from exc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return doc_id, ts


def clamp_limit(limit: int | None, *, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> int:
    if limit is None:
        return default
    return max(1, min(int(limit), maximum))


async def fetch_page[T](
    query: AsyncQuery | AsyncCollectionReference,
    *,
    collection: AsyncCollectionReference,
    order_field: str = "created_at",
    cursor: str | None,
    limit: int,
    descending: bool = True,
    parse: Callable[[DocumentSnapshot], T] | None = None,
) -> tuple[list[Any], str | None]:
    """One page of ``query`` (already filtered, not yet ordered) → ``(items, next_cursor)``.

    ``collection`` is the collection the documents live in (used to rebuild the tie-breaker reference).
    ``parse`` maps each snapshot to an item (default: the snapshot itself).
    """
    direction = firestore.Query.DESCENDING if descending else firestore.Query.ASCENDING
    q = query.order_by(order_field, direction=direction).order_by(
        FieldPath.document_id(), direction=direction
    )
    if cursor:
        doc_id, ts = decode_cursor(cursor)
        q = q.start_after({order_field: ts, FieldPath.document_id(): collection.document(doc_id)})
    q = q.limit(limit + 1)
    snaps = [snap async for snap in q.stream()]
    has_more = len(snaps) > limit
    snaps = snaps[:limit]
    next_cursor = None
    if has_more and snaps:
        last = snaps[-1]
        last_ts = (last.to_dict() or {}).get(order_field)
        if isinstance(last_ts, datetime):
            next_cursor = encode_cursor(last.id, last_ts)
    items = [parse(s) for s in snaps] if parse else list(snaps)
    return items, next_cursor
