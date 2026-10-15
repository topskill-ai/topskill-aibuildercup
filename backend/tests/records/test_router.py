from __future__ import annotations

import csv
import io

import pytest

from topskill.db import Col, get_db, new_id
from topskill.records import apply_values
from topskill.records.service import record_id
from topskill.testing.factories import make_conversation, make_user

from .helpers import VISA_SCHEMA, make_records_agent

pytestmark = pytest.mark.emulator


def H(user, role=None):
    headers = {"X-Dev-User": user.uid}
    if role:
        headers["X-Dev-Role"] = role
    return headers


async def _seed(agent, visitor, cid=None, **fields):
    cid = cid or new_id("cnv")
    res = await apply_values(
        agent=agent,
        version_n=1,
        conversation_id=cid,
        visitor=visitor,
        fields={k: {"value": v, "evidence": f"said {v}"} for k, v in fields.items()},
        complete=None,
        summary=None,
        source="tool",
    )
    assert res.ok
    return cid


async def test_list_masks_pii_paginates_and_filters(client) -> None:
    owner, agent = await make_records_agent()
    visitor = await make_user("visitor", email="ana@example.com", display_name="Ana")
    cids = [await _seed(agent, visitor, nationality="br", full_name=f"Ana {i}") for i in range(3)]
    await (
        get_db()
        .collection(Col.RECORDS)
        .document(record_id(agent.id, cids[0]))
        .update({"status": "completed", "follow_status": "done"})
    )
    r = await client.get(f"/api/agents/{agent.id}/records", headers=H(owner))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema"]["fields"][0]["key"] == "nationality" and body["next_cursor"] is None
    assert len(body["items"]) == 3
    row = body["items"][0]
    assert row["data"]["full_name"]["value"] == "•••" and row["data"]["nationality"]["value"] == "br"
    assert row["visitor_email_masked"] == "a***@example.com" and row["visitor_name"] == "Ana"
    assert set(row) == {
        "id",
        "conversation_id",
        "visitor_name",
        "visitor_email_masked",
        "version",
        "status",
        "summary",
        "completed_at",
        "data",
        "follow_status",
        "follow_note",
        "followed_at",
        "created_at",
        "updated_at",
    }
    revealed = (await client.get(f"/api/agents/{agent.id}/records?reveal_pii=1", headers=H(owner))).json()
    assert {i["data"]["full_name"]["value"] for i in revealed["items"]} == {"Ana 0", "Ana 1", "Ana 2"}

    page1 = (await client.get(f"/api/agents/{agent.id}/records?limit=2", headers=H(owner))).json()
    assert len(page1["items"]) == 2 and page1["next_cursor"]
    page2 = (
        await client.get(
            f"/api/agents/{agent.id}/records?limit=2&cursor={page1['next_cursor']}", headers=H(owner)
        )
    ).json()
    assert len(page2["items"]) == 1 and page2["next_cursor"] is None
    assert {i["id"] for i in page1["items"] + page2["items"]} == {record_id(agent.id, c) for c in cids}

    for query, expected in [
        ("status=completed", 1),
        ("status=in_progress", 2),
        ("follow_status=done", 1),
        ("status=completed&follow_status=new", 0),
    ]:
        items = (await client.get(f"/api/agents/{agent.id}/records?{query}", headers=H(owner))).json()[
            "items"
        ]
        assert len(items) == expected, query
    bad = await client.get(f"/api/agents/{agent.id}/records?status=nope", headers=H(owner))
    assert bad.status_code == 400 and bad.json()["code"] == "validation_error"


async def test_access_rules(client) -> None:
    _owner, agent = await make_records_agent()
    other = await make_user("creator")
    visitor = await make_user("visitor")
    r = await client.get(f"/api/agents/{agent.id}/records", headers=H(other))
    assert r.status_code == 404 and r.json()["code"] == "agent_not_found"
    r = await client.get(f"/api/agents/{agent.id}/records", headers=H(visitor))
    assert r.status_code == 403 and r.json()["code"] == "creator_required"
    r = await client.get(f"/api/agents/{agent.id}/records")
    assert r.status_code == 401
    r = await client.get(f"/api/agents/{agent.id}/records/export.csv", headers=H(other))
    assert r.status_code == 404


async def test_csv_export_endpoint(client) -> None:
    owner, agent = await make_records_agent()
    visitor = await make_user("visitor", email="ana@example.com", display_name="Ana Silva")
    await _seed(agent, visitor, nationality="br", visa_types=["work", "family"], full_name="=Ana")
    r = await client.get(f"/api/agents/{agent.id}/records/export.csv", headers=H(owner))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    disp = r.headers["content-disposition"]
    assert disp.startswith(f'attachment; filename="{agent.slug}-records-') and disp.endswith('Z.csv"')
    assert r.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    cell = dict(zip(rows[0], rows[1], strict=True))
    assert (
        cell["Nationality"] == "Brazil"
        and cell["Visa types"] == "Work; Family"
        and cell["Full name"] == "'=Ana"
    )
    assert cell["Visitor email (masked)"] == "a***@example.com"


async def test_validate_schema_endpoint_and_type_lock(client) -> None:
    owner, agent = await make_records_agent()
    url = f"/api/agents/{agent.id}/record-schema/validate"
    r = await client.post(url, json={"record_schema": {"fields": []}}, headers=H(owner))
    assert r.status_code == 200 and r.json() == {"ok": True, "record_schema": None}
    r = await client.post(url, json={"record_schema": None}, headers=H(owner))
    assert r.json() == {"ok": True, "record_schema": None}
    r = await client.post(url, json={"record_schema": VISA_SCHEMA}, headers=H(owner))
    assert r.status_code == 200 and r.json()["record_schema"]["fields"][0]["options"][0] == {
        "key": "br",
        "label": "Brazil",
    }
    assert r.json()["record_schema"]["version"] == 1 and r.json()["record_schema"]["scope"] == "session"
    r = await client.post(
        url,
        json={"record_schema": {"fields": [{"key": "Bad", "label": "x" * 50, "type": "text"}]}},
        headers=H(owner),
    )
    assert r.status_code == 400 and r.json()["code"] == "record_schema_invalid"
    assert len(r.json()["detail"]["errors"]) == 2

    # Changing the type of a field: allowed while it has no data, 409 once it has.
    changed = {"fields": [{**VISA_SCHEMA["fields"][2], "type": "text"}]}
    assert (await client.post(url, json={"record_schema": changed}, headers=H(owner))).status_code == 200
    await _seed(agent, await make_user("visitor"), budget_usd=500)
    r = await client.post(url, json={"record_schema": changed}, headers=H(owner))
    assert r.status_code == 409 and r.json()["code"] == "record_field_type_locked"
    assert r.json()["detail"] == {"key": "budget_usd"}
    # Removing the field or keeping its type is fine.
    keep = {"fields": [VISA_SCHEMA["fields"][2]]}
    assert (await client.post(url, json={"record_schema": keep}, headers=H(owner))).status_code == 200
    drop = {"fields": [VISA_SCHEMA["fields"][0]]}
    assert (await client.post(url, json={"record_schema": drop}, headers=H(owner))).status_code == 200


async def test_record_detail_patch_and_batch_follow(client) -> None:
    owner, agent = await make_records_agent()
    visitor = await make_user("visitor")
    conv = await make_conversation(agent, visitor, turns=[("I'm from Brazil", "Noted!")])
    await _seed(agent, visitor, cid=conv.id, nationality="br", full_name="Ana")
    rid = record_id(agent.id, conv.id)
    r = await client.get(f"/api/agents/{agent.id}/records/{rid}", headers=H(owner))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == rid and body["data"]["full_name"]["value"] == "•••"
    assert [m["text"] for m in body["transcript"]] == ["I'm from Brazil", "Noted!"]
    r = await client.get(f"/api/agents/{agent.id}/records/{rid}?reveal_pii=1", headers=H(owner))
    assert r.json()["data"]["full_name"]["value"] == "Ana"

    r = await client.patch(
        f"/api/agents/{agent.id}/records/{rid}",
        json={"follow_status": "following", "follow_note": " Call Friday "},
        headers=H(owner),
    )
    assert r.status_code == 200 and r.json()["follow_status"] == "following"
    assert r.json()["follow_note"] == "Call Friday" and r.json()["followed_at"]
    r = await client.patch(
        f"/api/agents/{agent.id}/records/{rid}", json={"follow_status": "bogus"}, headers=H(owner)
    )
    assert r.status_code == 400

    rid2 = record_id(agent.id, await _seed(agent, visitor, nationality="in"))
    r = await client.post(
        f"/api/agents/{agent.id}/records/follow",
        json={"ids": [rid, rid2, f"{agent.id}__cnv_missing"], "follow_status": "done"},
        headers=H(owner),
    )
    assert r.status_code == 200 and r.json() == {"updated": 2}
    r = await client.post(
        f"/api/agents/{agent.id}/records/follow", json={"ids": [], "follow_status": "done"}, headers=H(owner)
    )
    assert r.status_code == 400


async def test_idor_records_of_another_agent_are_404(client) -> None:
    owner, agent_a = await make_records_agent()
    other_owner, agent_b = await make_records_agent()
    visitor = await make_user("visitor")
    rid_b = record_id(agent_b.id, await _seed(agent_b, visitor, nationality="br"))
    # Owner of A addresses B's record through A's routes.
    for method, path, kwargs in [
        ("GET", f"/api/agents/{agent_a.id}/records/{rid_b}", {}),
        ("PATCH", f"/api/agents/{agent_a.id}/records/{rid_b}", {"json": {"follow_status": "invalid"}}),
    ]:
        r = await client.request(method, path, headers=H(owner), **kwargs)
        assert r.status_code == 404 and r.json()["code"] == "record_not_found", (method, r.text)
    # A forged id that carries A's prefix but points at B's data is also rejected.
    forged = f"{agent_a.id}__x"
    await (
        get_db()
        .collection(Col.RECORDS)
        .document(forged)
        .set({"id": forged, "agent_id": agent_b.id, "data": {}})
    )
    r = await client.get(f"/api/agents/{agent_a.id}/records/{forged}", headers=H(owner))
    assert r.status_code == 404 and r.json()["code"] == "record_not_found"
    # Batch follow drops foreign ids before reading.
    r = await client.post(
        f"/api/agents/{agent_a.id}/records/follow",
        json={"ids": [rid_b, forged], "follow_status": "invalid"},
        headers=H(owner),
    )
    assert r.json() == {"updated": 0}
    snap = await get_db().collection(Col.RECORDS).document(rid_b).get()
    assert snap.to_dict()["follow_status"] == "new"
    # B's own owner still sees it; A's list never includes it.
    items = (await client.get(f"/api/agents/{agent_a.id}/records", headers=H(owner))).json()["items"]
    assert rid_b not in {i["id"] for i in items}
    r = await client.get(f"/api/agents/{agent_b.id}/records/{rid_b}", headers=H(other_owner))
    assert r.status_code == 200
