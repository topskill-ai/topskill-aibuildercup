"""A2A v1.0 Agent Cards (SPEC §9.2)."""

from __future__ import annotations

from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentProvider,
    AgentSkill,
)

from topskill.config import get_settings
from topskill.domain import AgentDoc, AgentVersion, StarterQuestion

MANDATE_EXTENSION_URI = "https://topskill.dev/ext/ap2-intent-mandate/v1"
MANDATE_EXTENSION_DESCRIPTION = (
    "Paid agent. Attach an AP2-style intent mandate (EdDSA JWS) in the X-AP2-Mandate header or "
    "params.metadata.ap2_mandate."
)


def card_url(slug: str) -> str:
    return f"{get_settings().public_api_base}/a2a/{slug}/.well-known/agent-card.json"


def endpoint_url(slug: str) -> str:
    return f"{get_settings().public_api_base}/a2a/{slug}"


def starter_questions(agent: AgentDoc) -> list[StarterQuestion]:
    """The agent's starter questions; ``None`` → generic defaults built from capabilities/title."""
    if agent.starter_questions is not None:
        return list(agent.starter_questions)
    questions = [
        StarterQuestion(label=c.name[:60], prompt=f"Help me with {c.name.lower()}: {c.description}"[:300])
        for c in agent.capabilities[:3]
    ]
    generic = [
        StarterQuestion(label="What can you do?", prompt=f"What can {agent.title} help me with?"[:300]),
        StarterQuestion(label="Get started", prompt="Here is my situation. What should I do first?"),
        StarterQuestion(label="What do you need?", prompt="What information do you need from me?"),
    ]
    for q in generic:
        if len(questions) >= 3:
            break
        questions.append(q)
    return questions


def card_tags(agent: AgentDoc) -> list[str]:
    return [*[c.name for c in agent.capabilities][:6], "topskill"]


def build_card(agent: AgentDoc, version: AgentVersion) -> AgentCard:
    settings = get_settings()
    api, web, slug = settings.public_api_base, settings.public_web_base, agent.slug
    return AgentCard(
        name=agent.title,
        description=f"{agent.tagline}\n\n{agent.description}".strip()[:1000] or agent.title,
        version=str(version.n),
        supported_interfaces=[
            AgentInterface(url=endpoint_url(slug), protocol_binding="JSONRPC", protocol_version="1.0")
        ],
        provider=AgentProvider(organization="TopSkill", url=web),
        documentation_url=f"{web}/a/{slug}",
        capabilities=AgentCapabilities(
            streaming=True,
            extensions=[
                AgentExtension(
                    uri=MANDATE_EXTENSION_URI,
                    required=True,
                    description=MANDATE_EXTENSION_DESCRIPTION,
                    params={
                        "price_cents": agent.pricing.a2a_price_cents,
                        "currency": "TSC",
                        "jwks_url": f"{api}/.well-known/jwks.json",
                        "issuer": api,
                        "audience": f"topskill:agent:{slug}",
                        "mandate_endpoint": f"{api}/api/mandates",
                    },
                )
            ],
        ),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id=slug,
                name=agent.title,
                description=agent.tagline or agent.title,
                tags=card_tags(agent),
                examples=[q.prompt for q in starter_questions(agent)][:3],
            )
        ],
    )


def card_to_dict(card: AgentCard) -> dict:
    """Wire JSON of a card (a2a-sdk helper)."""
    return agent_card_to_dict(card)


def card_price_cents(card: dict) -> int | None:
    """``price_cents`` from the mandate extension of a card dict, if present."""
    for ext in ((card.get("capabilities") or {}).get("extensions")) or []:
        if ext.get("uri") == MANDATE_EXTENSION_URI:
            price = (ext.get("params") or {}).get("price_cents")
            if isinstance(price, int | float) and not isinstance(price, bool):
                return int(price)
    return None
