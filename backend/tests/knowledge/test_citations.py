from __future__ import annotations

import pytest
from google.genai import types

from topskill.domain import Passage
from topskill.knowledge import Collector, new_collector

pytestmark = pytest.mark.unit


def _p(file_id: str, page: int | None, text: str, filename: str = "guide.pdf") -> Passage:
    return Passage(file_id=file_id, filename=filename, text=text, page_start=page, page_end=page, score=None)


def _gm(web: list[tuple[str, str]], queries: list[str], entry: str | None = None) -> types.GroundingMetadata:
    return types.GroundingMetadata(
        web_search_queries=queries,
        grounding_chunks=[types.GroundingChunk(web=types.GroundingChunkWeb(uri=u, title=t)) for u, t in web],
        search_entry_point=types.SearchEntryPoint(rendered_content=entry) if entry else None,
    )


def test_passage_numbering_is_stable_and_deduplicated() -> None:
    c = new_collector()
    assert isinstance(c, Collector)
    a, b = _p("kf_a", 2, "Priority costs 480"), _p("kf_b", None, "Refunds", "faq.md")
    assert c.add_passages([a, b]) == [1, 2]
    assert c.add_passages([_p("kf_c", 1, "New"), a]) == [3, 1]
    sources = c.sources()
    assert [(s.n, s.kind, s.title, s.page_start) for s in sources] == [
        (1, "knowledge", "guide.pdf", 2),
        (2, "knowledge", "faq.md", None),
        (3, "knowledge", "guide.pdf", 1),
    ]
    assert (
        sources[0].file_id == "kf_a" and sources[0].snippet == "Priority costs 480" and sources[0].uri is None
    )


def test_snippet_is_clipped() -> None:
    c = Collector()
    c.add_passages([_p("kf_a", 1, "x" * 1000)])
    assert len(c.sources()[0].snippet or "") == 300


def test_grounding_web_sources_queries_and_entry_point() -> None:
    c = Collector()
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"
    new = c.add_grounding(_gm([(redirect, "gov.br"), (redirect, "gov.br")], ["brazil work visa"], "<p>1</p>"))
    assert new == ["brazil work visa"]
    assert c.add_grounding(_gm([], ["Brazil work visa", "visa fees"])) == ["visa fees"]
    assert c.add_grounding(None) == []
    c.add_grounding(_gm([("https://example.com/x", "Example page")], [], "<p>2</p>"))
    assert c.web_search_queries == ["brazil work visa", "visa fees"]
    assert c.search_entry_point_html == "<p>2</p>"
    # knowledge passages added after web grounding still come first; web numbered after them
    c.add_passages([_p("kf_a", 3, "Refund policy")])
    sources = c.sources()
    assert [(s.n, s.kind) for s in sources] == [(1, "knowledge"), (2, "web"), (3, "web")]
    assert sources[1].title == "gov.br" and sources[1].domain == "gov.br" and sources[1].uri == redirect
    assert sources[2].title == "Example page" and sources[2].domain == "example.com"


def test_grounding_ignores_non_web_chunks() -> None:
    c = Collector()
    gm = types.GroundingMetadata(
        grounding_chunks=[
            types.GroundingChunk(retrieved_context=types.GroundingChunkRetrievedContext(text="x", title="t"))
        ]
    )
    assert c.add_grounding(gm) == [] and c.sources() == [] and c.search_entry_point_html is None


def test_collectors_are_independent() -> None:
    a, b = new_collector(), new_collector()
    a.add_passages([_p("kf_a", 1, "x")])
    assert b.sources() == []
