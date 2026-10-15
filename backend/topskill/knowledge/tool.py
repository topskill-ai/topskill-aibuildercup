"""``search_knowledge`` function tool (SPEC §10.1).

``make_search_tool(agent_id, collector)`` wraps ``async def search_knowledge(query: str) -> dict``.
With a collector (web channel, one per turn) passage numbers come from ``collector.add_passages`` and stay
stable across searches in the turn; with ``collector=None`` (A2A, shared stack) passages are numbered
``1..k`` within each result. Failures never fail the turn: the model gets an empty result and a note.
"""

from __future__ import annotations

import logging

from google.adk.tools import FunctionTool

from topskill.domain import CitationCollector, Passage
from topskill.knowledge import service

log = logging.getLogger(__name__)

RESULT_TEXT_CHARS = 1200
MAX_OUTPUT_CHARS = 16_000
NOTE = "Cite as [n]. If nothing relevant, say the documents don't cover it."
UNAVAILABLE_NOTE = "The document search is temporarily unavailable. Answer without the documents and say so."


def pages_label(p: Passage) -> str:
    """``"2"`` / ``"2–3"`` / ``""`` when the page is unknown."""
    if p.page_start is None:
        return ""
    if p.page_end is None or p.page_end == p.page_start:
        return str(p.page_start)
    return f"{p.page_start}–{p.page_end}"


def _item(p: Passage, n: int) -> dict:
    text = p.text[:RESULT_TEXT_CHARS]
    return {"ref": f"[{n}]", "source": p.filename, "pages": pages_label(p), "text": text}


def fit_output(passages: list[Passage]) -> list[Passage]:
    """The leading passages whose tool output fits in ``MAX_OUTPUT_CHARS``."""
    used = len(NOTE) + 40
    kept: list[Passage] = []
    for p in passages:
        size = sum(len(str(v)) for v in _item(p, 99).values()) + 40
        if used + size > MAX_OUTPUT_CHARS:
            break
        used += size
        kept.append(p)
    return kept


def format_results(passages: list[Passage], numbers: list[int]) -> dict:
    """Tool output: ``{"results": [{ref, source, pages, text}], "note": …}``."""
    return {"results": [_item(p, n) for p, n in zip(passages, numbers, strict=True)], "note": NOTE}


def make_search_tool(agent_id: str, collector: CitationCollector | None) -> FunctionTool:
    async def search_knowledge(query: str) -> dict:
        """Search the creator's reference documents. Use for any question that may be covered by them.
        Cite results inline as [n]."""
        try:
            passages = await service.search(agent_id, query)
        except Exception:
            log.warning("search_knowledge_failed agent_id=%s", agent_id, exc_info=True)
            return {"results": [], "note": UNAVAILABLE_NOTE}
        passages = fit_output(passages)
        if collector is not None:
            numbers = collector.add_passages(passages)
        else:
            numbers = list(range(1, len(passages) + 1))
        return format_results(passages, numbers)

    return FunctionTool(search_knowledge)
