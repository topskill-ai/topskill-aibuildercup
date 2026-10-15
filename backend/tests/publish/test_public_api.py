"""Public agent pages and marketplace listing on the emulator."""

from __future__ import annotations

import httpx
import pytest

from tests.publish.helpers import creator, publish
from topskill.agents import deps
from topskill.db import Col, get_db, utcnow
from topskill.testing.factories import unique

pytestmark = pytest.mark.emulator

CARD_KEYS = {
    "slug",
    "title",
    "tagline",
    "avatar_url",
    "accent_color",
    "creator",
    "pricing",
    "features",
    "stats",
}
PUBLIC_KEYS = CARD_KEYS | {
    "description",
    "greeting",
    "input_placeholder",
    "starter_questions",
    "capabilities",
    "a2a",
    "status",
}


@pytest.fixture(autouse=True)
def _no_wallets(monkeypatch: pytest.MonkeyPatch) -> None:
    class _B:
        async def get_or_open_wallet(self, uid: str) -> int:
            return 0

    monkeypatch.setattr(deps, "billing", lambda: _B())


async def test_public_agent_page(client: httpx.AsyncClient) -> None:
    uid, headers = creator()
    agent = await publish(client, headers, starter_questions=None, creator_name="Ana Silva")
    resp = await client.get(f"/api/public/agents/{agent['slug']}")
    assert resp.status_code == 200, resp.text
    page = resp.json()
    assert set(page) == PUBLIC_KEYS
    assert page["status"] == "active" and page["title"] == "Travel Insurance Helper"
    assert page["creator"] == {"uid": uid, "name": "Ana Silva", "headline": ""}
    assert page["pricing"] == {"mode": "per_round", "price_cents": 100, "free_rounds": 2}
    assert page["features"] == {
        "knowledge": False,
        "web_search": True,
        "records": False,
        "a2a": True,
        "safety_screening": False,  # P2 guard: GUARD_BACKEND=off in tests
    }
    assert page["stats"] == {"conversations": 0, "a2a_calls": 0}
    assert len(page["starter_questions"]) == 3  # null → defaults
    assert page["starter_questions"][0]["label"] == "Cover levels"
    assert page["a2a"]["enabled"] is True and page["a2a"]["price_cents"] == 200
    assert page["a2a"]["card_url"].endswith(f"/a2a/{agent['slug']}/.well-known/agent-card.json")

    await client.patch(f"/api/agents/{agent['id']}", json={"starter_questions": []}, headers=headers)
    assert (await client.get(f"/api/public/agents/{agent['slug']}")).json()["starter_questions"] == []

    resp = await client.get("/api/public/agents/no-such-agent-here")
    assert resp.status_code == 404 and resp.json()["code"] == "agent_not_found"


async def test_features_follow_active_version_and_ready_knowledge(client: httpx.AsyncClient) -> None:
    _, headers = creator()
    agent = await publish(client, headers)
    schema = {"fields": [{"key": "destination", "label": "Destination", "type": "text"}]}
    await client.patch(
        f"/api/agents/{agent['id']}",
        json={"config": {"record_schema": schema, "web_search_enabled": False}},
        headers=headers,
    )
    page = (await client.get(f"/api/public/agents/{agent['slug']}")).json()
    assert page["features"]["records"] is False and page["features"]["web_search"] is True  # not built yet
    await client.post(f"/api/agents/{agent['id']}/versions", json={}, headers=headers)
    page = (await client.get(f"/api/public/agents/{agent['slug']}")).json()
    assert page["features"]["records"] is True and page["features"]["web_search"] is False

    # a ready knowledge file (written as the knowledge lane would) turns the badge on
    kref = get_db().collection(Col.AGENTS).document(agent["id"]).collection(Col.KNOWLEDGE).document("kf_test")
    now = utcnow()
    await kref.set({"file_id": "kf_test", "filename": "a.pdf", "status": "processing", "created_at": now})
    detail = (await client.get(f"/api/agents/{agent['id']}", headers=headers)).json()
    assert detail["knowledge"] == {"files": 1, "ready": 0, "processing": 1, "failed": 0}
    assert (await client.get(f"/api/public/agents/{agent['slug']}")).json()["features"]["knowledge"] is False
    await kref.update({"status": "ready"})
    assert (await client.get(f"/api/public/agents/{agent['slug']}")).json()["features"]["knowledge"] is True
    detail = (await client.get(f"/api/agents/{agent['id']}", headers=headers)).json()
    assert detail["knowledge"]["ready"] == 1


async def test_marketplace_listing_filters_search_and_pagination(client: httpx.AsyncClient) -> None:
    _, headers = creator()
    tag = unique("zq")
    made = []
    for i in range(3):
        made.append(await publish(client, headers, title=f"Agent {i}", tagline=f"Finds {tag} things"))
    paused = await publish(client, headers, title="Paused one", tagline=f"{tag} paused")
    await client.post(f"/api/agents/{paused['id']}/pause", headers=headers)
    hidden = await publish(client, headers, title="Hidden one", tagline=f"{tag} hidden", listed=False)
    deleted = await publish(client, headers, title="Deleted one", tagline=f"{tag} deleted")
    await client.delete(f"/api/agents/{deleted['id']}", headers=headers)

    resp = await client.get("/api/public/agents", params={"q": tag.upper()})
    assert resp.status_code == 200, resp.text
    page = resp.json()
    slugs = [c["slug"] for c in page["items"]]
    assert slugs == [m["slug"] for m in reversed(made)]
    assert page["next_cursor"] is None
    assert set(page["items"][0]) == CARD_KEYS
    for excluded in (paused, hidden, deleted):
        assert excluded["slug"] not in slugs

    first = (await client.get("/api/public/agents", params={"q": tag, "limit": 2})).json()
    assert [c["slug"] for c in first["items"]] == slugs[:2] and first["next_cursor"]
    second = (
        await client.get("/api/public/agents", params={"q": tag, "limit": 2, "cursor": first["next_cursor"]})
    ).json()
    assert [c["slug"] for c in second["items"]] == slugs[2:] and second["next_cursor"] is None

    resp = await client.get("/api/public/agents", params={"cursor": "garbage!"})
    assert resp.status_code == 400 and resp.json()["code"] == "validation_error"
    unfiltered = (await client.get("/api/public/agents", params={"limit": 5})).json()
    assert len(unfiltered["items"]) <= 5
