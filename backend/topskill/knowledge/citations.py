"""Per-turn citation collector (SPEC §7.3, §10.2; implements ``domain.CitationCollector``).

* Knowledge passages returned by ``search_knowledge`` are numbered ``1, 2, …`` in arrival order; a passage
  already numbered in this turn keeps its number (key ``(file_id, page_start, text[:80])``), so the
  ``[n]`` markers the model writes stay valid across several searches.
* Web sources from ``grounding_metadata.grounding_chunks[].web`` are deduplicated by ``uri`` and by
  ``(title, domain)`` (Google's redirect URIs differ per chunk even for one site), and numbered
  after every knowledge passage (they are listed, not inline-marked), so their numbers are assigned when
  :meth:`Collector.sources` is called.
* ``search_entry_point_html`` is the ``rendered_content`` of the last grounding metadata that had one.

One instance per web turn; never shared between turns.
"""

from __future__ import annotations

from urllib.parse import urlparse

from google.genai import types

from topskill.domain import Citation, CitationCollector, Passage

SNIPPET_CHARS = 300


def passage_key(p: Passage) -> tuple[str, int | None, str]:
    return (p.file_id, p.page_start, p.text[:80])


def _domain_of(uri: str | None) -> str | None:
    if not uri:
        return None
    host = urlparse(uri).netloc.lower()
    return host.removeprefix("www.") or None


class Collector:
    def __init__(self) -> None:
        self._knowledge: dict[tuple[str, int | None, str], Citation] = {}
        self._web: dict[str, tuple[str, str | None]] = {}  # uri → (title, domain), insertion ordered
        self._queries: list[str] = []
        self._seen_queries: set[str] = set()
        self._entry_point: str | None = None

    # ---- knowledge
    def add_passages(self, passages: list[Passage]) -> list[int]:
        numbers: list[int] = []
        for p in passages:
            key = passage_key(p)
            citation = self._knowledge.get(key)
            if citation is None:
                citation = Citation(
                    n=len(self._knowledge) + 1,
                    kind="knowledge",
                    title=p.filename,
                    file_id=p.file_id,
                    page_start=p.page_start,
                    page_end=p.page_end,
                    snippet=p.text[:SNIPPET_CHARS],
                )
                self._knowledge[key] = citation
            numbers.append(citation.n)
        return numbers

    # ---- web grounding
    def add_grounding(self, gm: types.GroundingMetadata | None) -> list[str]:
        """Record web sources, queries and the search entry point; → queries not seen before this call."""
        if gm is None:
            return []
        new_queries: list[str] = []
        for query in gm.web_search_queries or []:
            text = (query or "").strip()
            if text and text.lower() not in self._seen_queries:
                self._seen_queries.add(text.lower())
                self._queries.append(text)
                new_queries.append(text)
        for chunk in gm.grounding_chunks or []:
            web = chunk.web
            if web is None or not web.uri or web.uri in self._web:
                continue
            domain = getattr(web, "domain", None) or (web.title if web.title and "." in web.title else None)
            domain = domain or _domain_of(web.uri)
            title = web.title or domain or web.uri
            if (title, domain) in self._web.values():
                continue  # same label and domain (e.g. two redirect URIs to one site): one chip
            self._web[web.uri] = (title, domain)
        entry = gm.search_entry_point
        if entry is not None and entry.rendered_content:
            self._entry_point = entry.rendered_content
        return new_queries

    # ---- output
    def sources(self) -> list[Citation]:
        """Knowledge citations (by n) then web sources numbered after them."""
        knowledge = sorted(self._knowledge.values(), key=lambda c: c.n)
        base = len(knowledge)
        web = [
            Citation(n=base + i, kind="web", title=title, uri=uri, domain=domain)
            for i, (uri, (title, domain)) in enumerate(self._web.items(), start=1)
        ]
        return [*knowledge, *web]

    @property
    def search_entry_point_html(self) -> str | None:
        return self._entry_point

    @property
    def web_search_queries(self) -> list[str]:
        return list(self._queries)


def new_collector() -> CitationCollector:
    return Collector()
