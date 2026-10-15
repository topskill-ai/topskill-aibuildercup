from __future__ import annotations

import pytest

from topskill.domain import Passage
from topskill.knowledge import new_collector
from topskill.knowledge import service as knowledge_service
from topskill.knowledge.tool import MAX_OUTPUT_CHARS, NOTE, UNAVAILABLE_NOTE, make_search_tool, pages_label

pytestmark = pytest.mark.unit

P1 = Passage("kf_a", "visa_guide.pdf", "Priority costs 480.", 2, 2, 1.0)
P2 = Passage("kf_b", "faq.md", "Refunds within 45 days.", None, None, 0.5)
P3 = Passage("kf_a", "visa_guide.pdf", "Spans pages.", 2, 3, None)


@pytest.fixture
def fake_search(monkeypatch: pytest.MonkeyPatch):
    results: list[list[Passage]] = []

    async def _search(agent_id: str, query: str, top_k: int | None = None) -> list[Passage]:
        assert agent_id == "agt_1"
        return results.pop(0)

    monkeypatch.setattr(knowledge_service, "search", _search)
    return results


async def test_tool_with_collector_keeps_numbers_across_searches(fake_search) -> None:
    collector = new_collector()
    tool = make_search_tool("agt_1", collector)
    assert tool.name == "search_knowledge"
    assert "Cite results inline as [n]" in (tool.description or "")
    fake_search.extend([[P1, P2], [P3, P1]])
    first = await tool.func(query="priority")
    assert first == {
        "results": [
            {"ref": "[1]", "source": "visa_guide.pdf", "pages": "2", "text": "Priority costs 480."},
            {"ref": "[2]", "source": "faq.md", "pages": "", "text": "Refunds within 45 days."},
        ],
        "note": NOTE,
    }
    second = await tool.func(query="pages")
    assert [r["ref"] for r in second["results"]] == ["[3]", "[1]"] and second["results"][0]["pages"] == "2–3"
    assert [s.n for s in collector.sources()] == [1, 2, 3]


async def test_tool_without_collector_numbers_per_result(fake_search) -> None:
    tool = make_search_tool("agt_1", None)
    fake_search.extend([[P2, P1], [P1]])
    assert [r["ref"] for r in (await tool.func(query="a"))["results"]] == ["[1]", "[2]"]
    assert [r["ref"] for r in (await tool.func(query="b"))["results"]] == ["[1]"]


async def test_tool_output_is_capped(fake_search) -> None:
    big = [Passage(f"kf_{i}", "big.pdf", "y" * 5000, i, i, None) for i in range(20)]
    fake_search.append(big)
    collector = new_collector()
    out = await make_search_tool("agt_1", collector).func(query="y")
    assert len(str(out)) <= MAX_OUTPUT_CHARS
    assert all(len(r["text"]) == 1200 for r in out["results"]) and 0 < len(out["results"]) < 20
    assert len(collector.sources()) == len(out["results"])  # only what the model saw is citable


async def test_tool_failure_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*args, **kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(knowledge_service, "search", _boom)
    out = await make_search_tool("agt_1", new_collector()).func(query="x")
    assert out == {"results": [], "note": UNAVAILABLE_NOTE}


def test_pages_label() -> None:
    assert pages_label(P1) == "2" and pages_label(P2) == "" and pages_label(P3) == "2–3"
