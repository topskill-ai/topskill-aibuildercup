"""Hourly/daily per-user counters in ``rate_limits/{bucket}__{uid}__{window}`` (SPEC §5.1)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Literal

from google.cloud import firestore

from topskill.db import Col, get_db, utcnow
from topskill.errors import ApiError, Codes

Window = Literal["hour", "day"]


def window_bounds(now: datetime, window: Window) -> tuple[str, datetime]:
    """``(window key, window end)``: ``YYYYMMDDHH`` for hourly, ``YYYYMMDD`` for daily (UTC)."""
    if window == "hour":
        start = now.replace(minute=0, second=0, microsecond=0)
        return start.strftime("%Y%m%d%H"), start + timedelta(hours=1)
    if window == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.strftime("%Y%m%d"), start + timedelta(days=1)
    raise ValueError(f"unknown window: {window}")


def doc_id(bucket: str, uid: str, window_key: str) -> str:
    return f"{bucket}__{uid}__{window_key}"


@firestore.async_transactional
async def _increment(tx, ref, limit: int, expires_at: datetime) -> int | None:
    snap = await ref.get(transaction=tx)
    count = int((snap.to_dict() or {}).get("count", 0)) if snap.exists else 0
    if count >= limit:
        return None
    tx.set(ref, {"count": count + 1, "expires_at": expires_at})
    return count + 1


async def hit(bucket: str, uid: str, *, limit: int, window: Window) -> None:
    """Count one use; raises 429 ``rate_limited`` (``detail.retry_after_s``) once ``limit`` is reached."""
    now = utcnow()
    key, end = window_bounds(now, window)
    retry_after_s = max(1, math.ceil((end - now).total_seconds()))
    if limit <= 0:
        raise ApiError(
            429,
            Codes.RATE_LIMITED,
            "Too many requests. Please try again later.",
            {"retry_after_s": retry_after_s},
        )
    db = get_db()
    ref = db.collection(Col.RATE_LIMITS).document(doc_id(bucket, uid, key))
    result = await _increment(db.transaction(), ref, limit, end)
    if result is None:
        raise ApiError(
            429,
            Codes.RATE_LIMITED,
            "Too many requests. Please try again later.",
            {"retry_after_s": retry_after_s},
        )
