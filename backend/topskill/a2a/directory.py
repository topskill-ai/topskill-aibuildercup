"""Agent directory for A2A callers (SPEC §6.4 ``/api/public/a2a/directory``).

One indexed query (``listed == True, status == "active", deleted == False`` ordered by ``created_at``,
§5.3) over the 200 newest agents; ``pricing.a2a_enabled`` and the keyword score are applied **in
memory**.
"""

from __future__ import annotations

import math
import re

from google.cloud import firestore
from pydantic import BaseModel

from topskill.db import Col, get_db
from topskill.domain import AgentDoc

from .cards import card_tags, card_url, endpoint_url, starter_questions

SCAN_LIMIT = 200
TOP_N = 8

_TERM_RE = re.compile(r"\w+")
_STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from has have how i in is it its me my need needs of on "
    "or our please should so some than that the their them then there these this to us was we what when "
    "where which who why will with would you your quick check new help".split()
)
# Field weights of the keyword score.
_WEIGHTS = (("title", 3.0), ("tagline", 2.0), ("capabilities", 2.0), ("description", 1.0))


class DirectoryAgent(BaseModel):
    slug: str
    name: str
    tagline: str
    description: str
    card_url: str
    endpoint: str
    price_cents: int
    tags: list[str]
    examples: list[str]


def terms(text: str) -> list[str]:
    """Lower-cased query terms without stopwords and 1–2 letter words (order kept, deduplicated)."""
    seen: list[str] = []
    for term in _TERM_RE.findall(text.lower()):
        if len(term) < 3 or term in _STOPWORDS or term in seen:
            continue
        seen.append(term)
    return seen


def _stem(term: str) -> str:
    for suffix in ("ing", "ers", "er", "es", "s"):
        if len(term) > len(suffix) + 2 and term.endswith(suffix):
            return term[: -len(suffix)]
    return term


def _words(agent: AgentDoc) -> dict[str, list[str]]:
    fields = {
        "title": agent.title,
        "tagline": agent.tagline,
        "capabilities": " ".join(f"{c.name} {c.description}" for c in agent.capabilities),
        "description": agent.description,
    }
    return {name: [_stem(w) for w in _TERM_RE.findall(text.lower())] for name, text in fields.items()}


def _term_score(words: dict[str, list[str]], term: str) -> float:
    """Sum over fields of ``weight * log(1 + hits)`` (prefix match on word stems)."""
    stem = _stem(term)
    total = 0.0
    for name, weight in _WEIGHTS:
        hits = sum(1 for w in words[name] if w.startswith(stem) or (stem.startswith(w) and len(w) >= 4))
        if hits:
            total += weight * math.log(1 + hits)
    return total


def rank(agents: list[AgentDoc], query_terms: list[str]) -> list[tuple[float, AgentDoc]]:
    """Keyword score per agent; each term is weighted by its rarity among ``agents``
    (``log(1 + N / df)``), so a distinctive word outweighs one most agents share."""
    words = [_words(a) for a in agents]
    per_term = {t: [_term_score(w, t) for w in words] for t in query_terms}
    n = len(agents)
    idf = {t: math.log(1 + n / max(1, sum(1 for s in scores if s > 0))) for t, scores in per_term.items()}
    return [(sum(per_term[t][i] * idf[t] for t in query_terms), a) for i, a in enumerate(agents)]


def to_directory_agent(agent: AgentDoc) -> DirectoryAgent:
    return DirectoryAgent(
        slug=agent.slug,
        name=agent.title,
        tagline=agent.tagline,
        description=agent.description,
        card_url=card_url(agent.slug),
        endpoint=endpoint_url(agent.slug),
        price_cents=agent.pricing.a2a_price_cents,
        tags=card_tags(agent),
        examples=[q.prompt for q in starter_questions(agent)][:3],
    )


async def _candidates() -> list[AgentDoc]:
    query = (
        get_db()
        .collection(Col.AGENTS)
        .where(filter=firestore.FieldFilter("listed", "==", True))
        .where(filter=firestore.FieldFilter("status", "==", "active"))
        .where(filter=firestore.FieldFilter("deleted", "==", False))
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(SCAN_LIMIT)
    )
    agents = [AgentDoc.model_validate(snap.to_dict()) async for snap in query.stream()]
    return [a for a in agents if a.pricing.a2a_enabled]  # in memory (§5.3)


async def search(q: str | None, *, limit: int = TOP_N, fallback: bool = False) -> list[DirectoryAgent]:
    """Rank A2A-enabled agents for ``q``. Without ``q``: newest first. With ``q``: only agents that match
    at least one term, best first (ties keep recency); ``fallback=True`` returns the newest agents when
    nothing matches (used by the Enterprise Assistant)."""
    agents = await _candidates()
    query_terms = terms(q or "")
    if not query_terms:
        return [to_directory_agent(a) for a in agents[:limit]]
    scored = [(points, i, a) for i, (points, a) in enumerate(rank(agents, query_terms))]
    matched = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))
    if not matched and fallback:
        return [to_directory_agent(a) for a in agents[:limit]]
    return [to_directory_agent(a) for _, _, a in matched[:limit]]
