"""Creator agent API on the emulator: publish (T5), slugs, versions (T6), PATCH, pause/resume/delete,
draft and avatars."""

from __future__ import annotations

import asyncio
import io
import re

import httpx
import pytest
from PIL import Image

from tests.publish.helpers import create_body, creator, publish, upload_skill, visitor
from topskill import gemini
from topskill.agents import deps
from topskill.agents import draft as drafts
from topskill.db import Col, get_db
from topskill.repo import get_agent, get_version
from topskill.testing.factories import make_skill, unique

pytestmark = pytest.mark.emulator

AGENT_KEYS = {
    "id",
    "slug",
    "status",
    "listed",
    "title",
    "tagline",
    "description",
    "avatar_id",
    "avatar_url",
    "greeting",
    "input_placeholder",
    "starter_questions",
    "capabilities",
    "accent_color",
    "creator_name",
    "creator_headline",
    "pricing",
    "config",
    "config_dirty",
    "active_version",
    "version_count",
    "skills",
    "knowledge",
    "stats",
    "urls",
    "created_at",
    "updated_at",
    "guard_active",  # P2 guard (SPEC §18 "Guard")
}
SUMMARY_KEYS = {
    "id",
    "slug",
    "title",
    "tagline",
    "avatar_url",
    "accent_color",
    "status",
    "listed",
    "pricing",
    "active_version",
    "config_dirty",
    "stats",
    "updated_at",
}


class FakeBilling:
    def __init__(self) -> None:
        self.opened: list[str] = []

    async def get_or_open_wallet(self, uid: str) -> int:
        self.opened.append(uid)
        return 10_000


@pytest.fixture
def fake_billing(monkeypatch: pytest.MonkeyPatch) -> FakeBilling:
    fake = FakeBilling()
    monkeypatch.setattr(deps, "billing", lambda: fake)
    return fake


# --------------------------------------------------------------------------- publish


async def test_publish_creates_agent_version_slug_stats(client: httpx.AsyncClient, fake_billing) -> None:
    uid, headers = creator()
    agent = await publish(client, headers)
    assert set(agent) == AGENT_KEYS
    assert agent["id"].startswith("agt_") and re.fullmatch(
        r"travel-insurance-helper-[0-9a-f]{6}", agent["slug"]
    )
    assert agent["status"] == "active" and agent["listed"] is True
    assert agent["active_version"] == 1 and agent["version_count"] == 1 and agent["config_dirty"] is False
    assert agent["config"]["model"] == "gemini-3.8-flash" and agent["config"]["record_schema"] is None
    assert agent["config"]["temperature"] is None
    assert agent["creator_name"] == uid and agent["creator_headline"] == ""
    assert agent["knowledge"] == {"files": 0, "ready": 0, "processing": 0, "failed": 0}
    assert agent["stats"]["rounds"] == 0 and agent["stats"]["last_activity_at"] is None
    assert agent["skills"][0]["slug"] == "sample-skill"
    assert agent["urls"]["public_page"].endswith(f"/a/{agent['slug']}")
    assert agent["created_at"].endswith("Z")
    assert fake_billing.opened == [uid]  # publish opens the owner's wallet

    db = get_db()
    slug_doc = (await db.collection(Col.SLUGS).document(agent["slug"]).get()).to_dict()
    assert slug_doc["agent_id"] == agent["id"]
    version = await get_version(agent["id"], 1)
    assert version is not None and version.title == "Travel Insurance Helper"
    assert version.skills[0].skill_id == agent["config"]["skill_ids"][0]
    stats = (await db.collection(Col.AGENT_STATS).document(agent["id"]).get()).to_dict()
    assert stats["owner_uid"] == uid and stats["conversations"] == 0


async def test_publish_opens_real_wallet_when_billing_exists(client: httpx.AsyncClient) -> None:
    uid, headers = creator()
    await publish(client, headers)
    wallet = await get_db().collection(Col.WALLETS).document(uid).get()
    assert wallet.exists


async def test_custom_slug_free_pricing_and_records(client: httpx.AsyncClient, fake_billing) -> None:
    _, headers = creator()
    slug = unique("my-agent-")
    agent = await publish(
        client,
        headers,
        slug=f"  {slug.upper()} ",
        pricing={
            "mode": "free",
            "price_cents": 500,
            "free_rounds": 0,
            "a2a_enabled": False,
            "a2a_price_cents": 0,
        },
        config={
            "soul": "s",
            "model": "gemini-2.5-pro",
            "temperature": 0.7,
            "record_schema": {"fields": []},
        },
        starter_questions=None,
        creator_name="Ana Silva",
    )
    assert agent["slug"] == slug
    assert agent["pricing"]["price_cents"] == 0
    assert agent["config"]["record_schema"] is None and agent["config"]["temperature"] == 0.7
    assert agent["starter_questions"] is None and agent["creator_name"] == "Ana Silva"


@pytest.mark.parametrize(
    ("overrides", "status", "code"),
    [
        ({"pricing": {"mode": "per_round", "price_cents": 0}}, 400, "pricing_invalid"),
        ({"pricing": {"mode": "subscription", "price_cents": 100}}, 400, "validation_error"),
        ({"config": {"soul": "s", "model": "gemini-1.0-pro"}}, 400, "model_not_allowed"),
        ({"slug": "studio"}, 400, "slug_reserved"),
        ({"slug": "x"}, 400, "slug_invalid"),
        ({"title": "   "}, 400, "validation_error"),
        ({"avatar_id": "av_00000000000000000000"}, 400, "validation_error"),
        ({"skill_ids": []}, 400, "validation_error"),
    ],
)
async def test_publish_validation(client: httpx.AsyncClient, fake_billing, overrides, status, code) -> None:
    _, headers = creator()
    skill = await upload_skill(client, headers)
    body = create_body([skill["id"]])
    body.update(overrides)
    resp = await client.post("/api/agents", json=body, headers=headers)
    assert resp.status_code == status and resp.json()["code"] == code, resp.text


async def test_publish_with_someone_elses_skill_is_404(client: httpx.AsyncClient, fake_billing) -> None:
    other = await make_skill(unique("u_"))
    _, headers = creator()
    resp = await client.post("/api/agents", json=create_body([other.skill_id]), headers=headers)
    assert resp.status_code == 404 and resp.json()["code"] == "skill_not_found"


async def test_slug_taken_and_slug_check(client: httpx.AsyncClient, fake_billing) -> None:
    _, headers = creator()
    slug = unique("taken-")
    check = (await client.get("/api/agents/slug-check", params={"slug": slug}, headers=headers)).json()
    assert check == {"slug": slug, "valid": True, "available": True, "reason": None}
    await publish(client, headers, slug=slug)
    check = (await client.get("/api/agents/slug-check", params={"slug": slug}, headers=headers)).json()
    assert check == {"slug": slug, "valid": True, "available": False, "reason": "slug_taken"}
    for bad, reason in (("api", "slug_reserved"), ("A B", "slug_invalid"), ("", "slug_invalid")):
        resp = (await client.get("/api/agents/slug-check", params={"slug": bad}, headers=headers)).json()
        assert resp["valid"] is False and resp["available"] is False and resp["reason"] == reason

    skill = await upload_skill(client, headers)
    resp = await client.post("/api/agents", json=create_body([skill["id"]], slug=slug), headers=headers)
    assert resp.status_code == 400 and resp.json()["code"] == "slug_taken"


async def test_slug_taken_race_only_one_wins(client: httpx.AsyncClient, fake_billing) -> None:
    _, headers = creator()
    skill = await upload_skill(client, headers)
    slug = unique("race-")
    body = create_body([skill["id"]], slug=slug)
    results = await asyncio.gather(
        *(client.post("/api/agents", json=body, headers=headers) for _ in range(5))
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 400, 400, 400, 400], [r.text for r in results]
    assert all(r.json()["code"] == "slug_taken" for r in results if r.status_code == 400)
    winner = next(r.json() for r in results if r.status_code == 201)
    slug_doc = (await get_db().collection(Col.SLUGS).document(slug).get()).to_dict()
    assert slug_doc["agent_id"] == winner["id"]


# --------------------------------------------------------------------------- reads and ownership


async def test_list_get_and_ownership(client: httpx.AsyncClient, fake_billing) -> None:
    _, headers = creator()
    a1 = await publish(client, headers, title="First Agent")
    a2 = await publish(client, headers, title="Second Agent")
    items = (await client.get("/api/agents", headers=headers)).json()["items"]
    assert [i["id"] for i in items] == [a2["id"], a1["id"]]
    assert set(items[0]) == SUMMARY_KEYS

    assert (await client.get(f"/api/agents/{a1['id']}", headers=headers)).json()["id"] == a1["id"]
    _, other = creator()
    for method, path in (
        ("GET", f"/api/agents/{a1['id']}"),
        ("PATCH", f"/api/agents/{a1['id']}"),
        ("POST", f"/api/agents/{a1['id']}/pause"),
        ("POST", f"/api/agents/{a1['id']}/versions"),
        ("DELETE", f"/api/agents/{a1['id']}"),
    ):
        resp = await client.request(method, path, json={} if method != "GET" else None, headers=other)
        assert resp.status_code == 404 and resp.json()["code"] == "agent_not_found", (method, path)
    _, vis = visitor()
    resp = await client.get(f"/api/agents/{a1['id']}", headers=vis)
    assert resp.status_code == 403 and resp.json()["code"] == "creator_required"


# --------------------------------------------------------------------------- PATCH and versions


async def test_patch_live_fields_do_not_dirty(client: httpx.AsyncClient, fake_billing) -> None:
    _, headers = creator()
    agent = await publish(client, headers)
    resp = await client.patch(
        f"/api/agents/{agent['id']}",
        json={
            "title": "Renamed",
            "tagline": "New tagline",
            "listed": False,
            "starter_questions": None,
            "pricing": {
                "mode": "per_round",
                "price_cents": 250,
                "free_rounds": 1,
                "a2a_enabled": True,
                "a2a_price_cents": 300,
            },
            "creator_headline": "Travel expert",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["title"] == "Renamed" and out["listed"] is False and out["starter_questions"] is None
    assert out["pricing"]["price_cents"] == 250 and out["creator_headline"] == "Travel expert"
    assert out["config_dirty"] is False and out["active_version"] == 1
    assert out["greeting"] == agent["greeting"]  # absent = unchanged

    resp = await client.patch(
        f"/api/agents/{agent['id']}",
        json={"pricing": {"mode": "per_round", "price_cents": 0}},
        headers=headers,
    )
    assert resp.status_code == 400 and resp.json()["code"] == "pricing_invalid"
    resp = await client.patch(f"/api/agents/{agent['id']}", json={"title": None}, headers=headers)
    assert resp.status_code == 400 and resp.json()["code"] == "validation_error"


async def test_patch_config_dirty_then_new_version(client: httpx.AsyncClient, fake_billing) -> None:
    uid, headers = creator()
    agent = await publish(client, headers)
    path = f"/api/agents/{agent['id']}"
    out = (await client.patch(path, json={"config": {"soul": "A new soul."}}, headers=headers)).json()
    assert out["config_dirty"] is True and out["config"]["soul"] == "A new soul."
    assert out["config"]["skill_ids"] == agent["config"]["skill_ids"]
    # editing back to the active version's config clears the flag
    back = (
        await client.patch(path, json={"config": {"soul": agent["config"]["soul"]}}, headers=headers)
    ).json()
    assert back["config_dirty"] is False

    await client.patch(
        path, json={"config": {"soul": "Soul v2", "web_search_enabled": False}}, headers=headers
    )
    await client.post(f"{path}/pause", headers=headers)
    resp = await client.post(f"{path}/versions", json={"note": "Tighter persona"}, headers=headers)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["active_version"] == 2 and out["version_count"] == 2
    assert out["config_dirty"] is False and out["status"] == "active"
    v2 = await get_version(agent["id"], 2)
    assert v2 is not None and v2.soul == "Soul v2" and v2.web_search_enabled is False
    assert v2.note == "Tighter persona" and v2.created_by == uid

    versions = (await client.get(f"{path}/versions", headers=headers)).json()["items"]
    assert [v["n"] for v in versions] == [2, 1] and versions[0]["active"] is True
    assert versions[1]["skills"][0]["skill_id"] == agent["config"]["skill_ids"][0]

    # rollback: config untouched, dirty because it differs from v1
    out = (await client.post(f"{path}/versions/1/activate", headers=headers)).json()
    assert out["active_version"] == 1 and out["config_dirty"] is True and out["config"]["soul"] == "Soul v2"
    resp = await client.post(f"{path}/versions/9/activate", headers=headers)
    assert resp.status_code == 404
    # empty body is accepted
    assert (await client.post(f"{path}/versions", headers=headers)).json()["active_version"] == 3


async def test_patch_skill_ids_must_be_owned(client: httpx.AsyncClient, fake_billing) -> None:
    uid, headers = creator()
    agent = await publish(client, headers)
    foreign = await make_skill(unique("u_"))
    resp = await client.patch(
        f"/api/agents/{agent['id']}", json={"config": {"skill_ids": [foreign.skill_id]}}, headers=headers
    )
    assert resp.status_code == 404 and resp.json()["code"] == "skill_not_found"
    own = await make_skill(uid)
    out = (
        await client.patch(
            f"/api/agents/{agent['id']}",
            json={"config": {"skill_ids": [own.skill_id, agent["config"]["skill_ids"][0]]}},
            headers=headers,
        )
    ).json()
    assert out["config"]["skill_ids"] == [own.skill_id, agent["config"]["skill_ids"][0]]
    assert out["config_dirty"] is True and len(out["skills"]) == 2


async def test_new_version_revalidates_skill_ownership(client: httpx.AsyncClient, fake_billing) -> None:
    _, headers = creator()
    agent = await publish(client, headers)
    foreign = await make_skill(unique("u_"))
    await (
        get_db().collection(Col.AGENTS).document(agent["id"]).update({"config.skill_ids": [foreign.skill_id]})
    )
    resp = await client.post(f"/api/agents/{agent['id']}/versions", json={}, headers=headers)
    assert resp.status_code == 404 and resp.json()["code"] == "skill_not_found"
    stored = await get_agent(agent["id"])
    assert stored is not None and stored.version_count == 1


async def test_new_version_uses_skills_stored_at_build_time(client: httpx.AsyncClient, fake_billing) -> None:
    """A PATCH of ``config.skill_ids`` racing ``POST /versions`` never yields a version with stale skills."""
    from topskill.agents import service
    from topskill.auth import CurrentUser

    uid, headers = creator()
    agent = await publish(client, headers)
    stale = await get_agent(agent["id"])
    other = await make_skill(uid)
    await get_db().collection(Col.AGENTS).document(agent["id"]).update({"config.skill_ids": [other.skill_id]})
    user = CurrentUser(uid=uid, email=None, display_name="Creator", role="creator")
    assert stale is not None
    updated = await service.create_version(stale, user, "race")
    assert updated.active_version == 2
    version = await get_version(agent["id"], 2)
    assert version is not None and [s.skill_id for s in version.skills] == [other.skill_id]


async def test_patch_record_schema(client: httpx.AsyncClient, fake_billing) -> None:
    _, headers = creator()
    agent = await publish(client, headers)
    path = f"/api/agents/{agent['id']}"
    schema = {
        "fields": [
            {"key": "destination", "label": "Destination", "type": "text", "required": True},
            {"key": "trip_days", "label": "Trip length", "type": "number", "min": 1, "max": 365},
        ]
    }
    out = (await client.patch(path, json={"config": {"record_schema": schema}}, headers=headers)).json()
    assert [f["key"] for f in out["config"]["record_schema"]["fields"]] == ["destination", "trip_days"]
    assert out["config_dirty"] is True
    out = (
        await client.patch(path, json={"config": {"record_schema": {"fields": []}}}, headers=headers)
    ).json()
    assert out["config"]["record_schema"] is None and out["config_dirty"] is False
    out = (await client.patch(path, json={"config": {"record_schema": None}}, headers=headers)).json()
    assert out["config"]["record_schema"] is None


async def test_pause_resume_delete(client: httpx.AsyncClient, fake_billing) -> None:
    _, headers = creator()
    agent = await publish(client, headers)
    path = f"/api/agents/{agent['id']}"
    assert (await client.post(f"{path}/pause", headers=headers)).json()["status"] == "paused"
    assert (await client.get(f"/api/public/agents/{agent['slug']}")).status_code == 410
    assert (await client.post(f"{path}/resume", headers=headers)).json()["status"] == "active"
    assert (await client.get(f"/api/public/agents/{agent['slug']}")).status_code == 200

    assert (await client.delete(path, headers=headers)).json() == {"ok": True}
    stored = await get_agent(agent["id"])
    assert stored is not None and stored.deleted and stored.status == "paused" and not stored.listed
    resp = await client.get(f"/api/public/agents/{agent['slug']}")
    assert resp.status_code == 410 and resp.json()["code"] == "agent_offline"
    assert (await client.get(path, headers=headers)).status_code == 404
    assert agent["id"] not in [
        a["id"] for a in (await client.get("/api/agents", headers=headers)).json()["items"]
    ]
    check = (
        await client.get("/api/agents/slug-check", params={"slug": agent["slug"]}, headers=headers)
    ).json()
    assert check["available"] is False  # slug stays reserved


# --------------------------------------------------------------------------- draft


async def test_draft_endpoint_with_mocked_model(client: httpx.AsyncClient, monkeypatch) -> None:
    seen: dict = {}

    async def fake_structured(chain, contents, schema, **kwargs):
        seen["contents"] = contents
        return (
            drafts.DraftModel.model_validate(
                {
                    "title": "Travel Cover Guide",
                    "tagline": "Find the right travel insurance.",
                    "description": "d",
                    "soul": "You are...",
                    "greeting": "Hi there",
                    "input_placeholder": "Where are you going?",
                    "starter_questions": [{"label": f"Q{i}", "prompt": f"Question {i}?"} for i in range(3)],
                    "capabilities": [{"icon": "shield-check", "name": "Cover", "description": "Levels"}],
                    "accent_color": "emerald",
                    "pricing": {"mode": "per_round", "price_cents": 100, "free_rounds": 2, "rationale": "r"},
                    "record_fields": [],
                    "web_search_recommended": False,
                    "avatar_prompt": "A suitcase",
                }
            ),
            chain[0],
        )

    monkeypatch.setattr(gemini, "generate_structured", fake_structured)
    _, headers = creator()
    skill = await upload_skill(client, headers)
    resp = await client.post("/api/agents/draft", json={"skill_ids": [skill["id"]]}, headers=headers)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["title"] == "Travel Cover Guide" and out["model_used"] == gemini.model_chain("draft")[0]
    assert len(out["starter_questions"]) == 3 and out["record_fields"] == []
    assert "Travel insurance helper" in seen["contents"] and "Sample Skill" in seen["contents"]

    foreign = await make_skill(unique("u_"))
    resp = await client.post("/api/agents/draft", json={"skill_ids": [foreign.skill_id]}, headers=headers)
    assert resp.status_code == 404 and resp.json()["code"] == "skill_not_found"
    resp = await client.post("/api/agents/draft", json={"skill_ids": []}, headers=headers)
    assert resp.status_code == 400 and resp.json()["code"] == "validation_error"


async def test_draft_rate_limited(client: httpx.AsyncClient, monkeypatch) -> None:
    from tests.core.helpers import settings_env

    async def fake_structured(*args, **kwargs):
        raise AssertionError("must not be called")

    monkeypatch.setattr(gemini, "generate_structured", fake_structured)
    _, headers = creator()
    skill = await upload_skill(client, headers)
    with settings_env(monkeypatch, draft_rate_limit_per_hour="0"):
        resp = await client.post("/api/agents/draft", json={"skill_ids": [skill["id"]]}, headers=headers)
    assert resp.status_code == 429 and resp.json()["code"] == "rate_limited"
    assert resp.json()["detail"]["retry_after_s"] > 0


# --------------------------------------------------------------------------- avatars


def _png(size=(800, 500)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (20, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


async def test_avatar_upload_and_public_serve(client: httpx.AsyncClient, fake_billing) -> None:
    _, headers = creator()
    resp = await client.post("/api/avatars", files={"file": ("me.png", _png(), "image/png")}, headers=headers)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert re.fullmatch(r"av_[0-9a-f]{20}", out["avatar_id"])
    assert out["avatar_url"].endswith(f"/api/public/avatars/{out['avatar_id']}")

    img = await client.get(f"/api/public/avatars/{out['avatar_id']}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/webp"
    assert img.headers["cache-control"] == "public, max-age=31536000, immutable"
    with Image.open(io.BytesIO(img.content)) as decoded:
        assert decoded.size == (384, 384)

    agent = await publish(client, headers, avatar_id=out["avatar_id"])
    assert agent["avatar_id"] == out["avatar_id"] and agent["avatar_url"] == out["avatar_url"]

    for bad in ("av_ffffffffffffffffffff", "../etc", "nope"):
        resp = await client.get(f"/api/public/avatars/{bad}")
        assert resp.status_code == 404 and resp.json()["code"] == "not_found"


async def test_avatar_upload_rejections(client: httpx.AsyncClient) -> None:
    _, headers = creator()
    resp = await client.post(
        "/api/avatars", files={"file": ("a.gif", b"GIF89a....", "image/gif")}, headers=headers
    )
    assert resp.status_code == 400 and resp.json()["code"] == "unsupported_file_type"
    big = b"\0" * (5 * 1024 * 1024 + 1)
    resp = await client.post("/api/avatars", files={"file": ("a.png", big, "image/png")}, headers=headers)
    assert resp.status_code == 413 and resp.json()["code"] == "payload_too_large"
    resp = await client.post("/api/avatars", files={"other": ("a.png", _png(), "image/png")}, headers=headers)
    assert resp.status_code == 400


async def test_avatar_generate_with_mocked_model(client: httpx.AsyncClient, monkeypatch) -> None:
    prompts: list[str] = []

    async def fake_image(prompt: str):
        prompts.append(prompt)
        return _png((1024, 1024)), "image/png"

    monkeypatch.setattr(gemini, "generate_image", fake_image)
    _, headers = creator()
    resp = await client.post(
        "/api/avatars/generate", json={"prompt": "A suitcase", "accent_color": "amber"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert "amber tones" in prompts[0] and prompts[0].startswith("A suitcase")
    assert (await client.get(f"/api/public/avatars/{resp.json()['avatar_id']}")).status_code == 200

    async def broken_image(prompt: str):
        return b"not an image", "image/png"

    monkeypatch.setattr(gemini, "generate_image", broken_image)
    resp = await client.post("/api/avatars/generate", json={"prompt": "x"}, headers=headers)
    assert resp.status_code == 502 and resp.json()["code"] == "image_failed"
    resp = await client.post("/api/avatars/generate", json={"prompt": "  "}, headers=headers)
    assert resp.status_code == 400
