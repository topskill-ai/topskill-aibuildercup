"""Agent Card JSON (SPEC §9.2) and the A2A directory (§6.4)."""

from __future__ import annotations

import pytest

from topskill.a2a import directory
from topskill.a2a.cards import MANDATE_EXTENSION_URI, card_price_cents
from topskill.testing.factories import make_agent, make_user, unique

pytestmark = pytest.mark.emulator


async def test_card_shape(client) -> None:
    owner = await make_user("creator")
    agent = await make_agent(
        owner,
        slug=f"visa-{unique('')}",
        title="Visa Advisor",
        tagline="Work visa plans in minutes",
        description="Explains work visa routes.",
        pricing={"a2a_price_cents": 250},
        capabilities=[{"icon": "plane", "name": "Visa routes", "description": "Compare visa options"}],
        starter_questions=[{"label": "Brazil", "prompt": "I am from Brazil. Which visa fits me?"}],
    )
    resp = await client.get(f"/a2a/{agent.slug}/.well-known/agent-card.json")
    assert resp.status_code == 200, resp.text
    card = resp.json()
    assert card["name"] == "Visa Advisor"
    assert card["description"] == "Work visa plans in minutes\n\nExplains work visa routes."
    assert card["version"] == "1"
    [iface] = card["supportedInterfaces"]
    assert iface == {
        "url": f"http://127.0.0.1:8000/a2a/{agent.slug}",
        "protocolBinding": "JSONRPC",
        "protocolVersion": "1.0",
    }
    assert card["provider"] == {"organization": "TopSkill", "url": "http://localhost:5173"}
    assert card["documentationUrl"] == f"http://localhost:5173/a/{agent.slug}"
    assert card["capabilities"]["streaming"] is True
    [ext] = card["capabilities"]["extensions"]
    assert ext["uri"] == MANDATE_EXTENSION_URI and ext["required"] is True
    assert ext["params"]["audience"] == f"topskill:agent:{agent.slug}"
    assert ext["params"]["jwks_url"] == "http://127.0.0.1:8000/.well-known/jwks.json"
    assert ext["params"]["mandate_endpoint"] == "http://127.0.0.1:8000/api/mandates"
    assert card_price_cents(card) == 250
    assert type(ext["params"]["price_cents"]) is int  # integer cents on the wire, not 250.0
    [skill] = card["skills"]
    assert skill["id"] == agent.slug and skill["tags"] == ["Visa routes", "topskill"]
    assert skill["examples"] == ["I am from Brazil. Which visa fits me?"]
    assert card["defaultInputModes"] == ["text/plain"] and card["defaultOutputModes"] == ["text/plain"]


async def test_card_errors_are_json_envelopes(client) -> None:
    owner = await make_user("creator")
    resp = await client.get("/a2a/no-such-agent-xyz/.well-known/agent-card.json")
    assert resp.status_code == 404 and resp.json()["code"] == "agent_not_found"
    disabled = await make_agent(owner, pricing={"a2a_enabled": False})
    resp = await client.get(f"/a2a/{disabled.slug}/.well-known/agent-card.json")
    assert resp.status_code == 404 and resp.json()["code"] == "agent_not_found"
    paused = await make_agent(owner, status="paused")
    resp = await client.get(f"/a2a/{paused.slug}/.well-known/agent-card.json")
    assert resp.status_code == 410 and resp.json()["code"] == "agent_offline"
    assert resp.json()["trace_id"]


def test_terms_and_score() -> None:
    assert directory.terms("Our new hire from Brazil needs a work visa plan") == [
        "hire",
        "brazil",
        "work",
        "visa",
        "plan",
    ]


async def test_directory_ranks_and_filters(client) -> None:
    owner = await make_user("creator")
    tag = unique("z")  # unique word so this test only sees its own agents
    visa = await make_agent(
        owner, title=f"Visa Advisor {tag}", tagline="Work visa plans", pricing={"a2a_price_cents": 200}
    )
    resume = await make_agent(
        owner,
        title=f"Resume Reviewer {tag}",
        tagline="Sharper resumes",
        capabilities=[{"icon": "file-text", "name": "Resume review", "description": "Line-by-line feedback"}],
    )
    await make_agent(owner, title=f"Hidden {tag} visa", pricing={"a2a_enabled": False})
    await make_agent(owner, title=f"Unlisted {tag} visa", listed=False)
    await make_agent(owner, title=f"Paused {tag} visa", status="paused")

    resp = await client.get("/api/public/a2a/directory", params={"q": f"{tag} visa"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items[0]["slug"] == visa.slug
    first = items[0]
    assert set(first) == {
        "slug",
        "name",
        "tagline",
        "description",
        "card_url",
        "endpoint",
        "price_cents",
        "tags",
        "examples",
    }
    assert first["card_url"] == f"http://127.0.0.1:8000/a2a/{visa.slug}/.well-known/agent-card.json"
    assert first["endpoint"] == f"http://127.0.0.1:8000/a2a/{visa.slug}"
    assert first["price_cents"] == 200 and first["tags"] == ["topskill"] and len(first["examples"]) == 3

    # only listed, active, A2A-enabled agents carry the tag
    resp = await client.get("/api/public/a2a/directory", params={"q": tag})
    assert {i["slug"] for i in resp.json()["items"]} == {visa.slug, resume.slug}

    resp = await client.get("/api/public/a2a/directory", params={"q": f"{tag} resume feedback"})
    assert resp.json()["items"][0]["slug"] == resume.slug

    # no query → newest first, capped at 8
    resp = await client.get("/api/public/a2a/directory")
    assert len(resp.json()["items"]) <= 8

    assert await directory.search("qqqqqq xxyyzz", fallback=False) == []
    assert len(await directory.search("qqqqqq xxyyzz", fallback=True)) >= 1
