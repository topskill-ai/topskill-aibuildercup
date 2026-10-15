from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from google.cloud.firestore_v1.base_query import FieldFilter

from topskill import ratelimit
from topskill.db import Col, get_db, utcnow
from topskill.errors import ApiError
from topskill.pagination import clamp_limit, decode_cursor, encode_cursor, fetch_page
from topskill.testing.factories import unique


@pytest.mark.unit
def test_window_bounds() -> None:
    now = datetime(2026, 10, 16, 8, 30, 15, tzinfo=UTC)
    assert ratelimit.window_bounds(now, "hour") == ("2026101608", datetime(2026, 10, 16, 9, tzinfo=UTC))
    assert ratelimit.window_bounds(now, "day") == ("20261016", datetime(2026, 10, 17, tzinfo=UTC))


@pytest.mark.emulator
async def test_hit_limits_and_doc_shape() -> None:
    uid = unique("u_")
    for _ in range(3):
        await ratelimit.hit("draft", uid, limit=3, window="hour")
    with pytest.raises(ApiError) as err:
        await ratelimit.hit("draft", uid, limit=3, window="hour")
    assert err.value.status == 429 and err.value.code == "rate_limited"
    assert 1 <= err.value.detail["retry_after_s"] <= 3600
    key, _ = ratelimit.window_bounds(utcnow(), "hour")
    doc = (await get_db().collection(Col.RATE_LIMITS).document(f"draft__{uid}__{key}").get()).to_dict()
    assert doc["count"] == 4 and doc["expires_at"] > utcnow()  # the rejected hit is counted too
    # other bucket / window are independent
    await ratelimit.hit("avatar", uid, limit=1, window="day")


@pytest.mark.emulator
async def test_hit_concurrent_never_exceeds_limit() -> None:
    uid = unique("u_")
    results = await asyncio.gather(
        *(ratelimit.hit("burst", uid, limit=5, window="day") for _ in range(8)), return_exceptions=True
    )
    assert sum(1 for r in results if r is None) == 5
    assert all(isinstance(r, ApiError) and r.code == "rate_limited" for r in results if r is not None)


@pytest.mark.unit
def test_cursor_roundtrip_and_errors() -> None:
    ts = datetime(2026, 10, 16, 8, 30, 15, 123456, tzinfo=UTC)
    assert decode_cursor(encode_cursor("ledger_doc", ts)) == ("ledger_doc", ts)
    for bad in ("", "!!!", "e30", encode_cursor("a/b", ts)):
        with pytest.raises(ApiError) as err:
            decode_cursor(bad)
        assert err.value.status == 400 and err.value.code == "validation_error"
    assert clamp_limit(None) == 50 and clamp_limit(500) == 100 and clamp_limit(0) == 1


@pytest.mark.emulator
async def test_fetch_page_walks_all_docs_with_ties() -> None:
    db = get_db()
    owner = unique("o_")
    col = db.collection(f"test_pages_{unique()}")
    base = utcnow().replace(microsecond=0)
    batch = db.batch()
    for i in range(7):
        # pairs share a timestamp to exercise the document-id tie-breaker
        batch.set(col.document(f"d{i}"), {"owner": owner, "created_at": base + timedelta(seconds=i // 2)})
    await batch.commit()
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        items, cursor = await fetch_page(
            col.where(filter=FieldFilter("owner", "==", owner)),
            collection=col,
            cursor=cursor,
            limit=3,
            parse=lambda s: s.id,
        )
        seen.extend(items)
        pages += 1
        if cursor is None:
            break
    assert pages == 3
    assert seen == ["d6", "d5", "d4", "d3", "d2", "d1", "d0"]
