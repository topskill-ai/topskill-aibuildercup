"""File Search backend without network: response mapping (recorded fixture), page spans, model fallback."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors
from google.genai import types

from tests.core.helpers import settings_env
from tests.knowledge.docs import make_pdf
from topskill.knowledge import file_search
from topskill.knowledge.base import NOT_CONFIGURED_ERROR, IndexingError
from topskill.knowledge.extract import extract_text
from topskill.knowledge.file_search import FileSearchBackend, PageIndex, pages_path
from topskill.storage import get_blob_store

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).parent / "fixtures" / "file_search_response.json"
FILE_ID = "kf_0123456789abcdef0123"
PAGES = [
    "Acme Visa Guide - Chapter 1: Eligibility\nApplicants for the Acme Student Visa must be at least 17 years old.",
    "Chapter 2: Processing Times\nPriority service costs 480 Zorkmids and reduces processing to 5 working days.",
    "Chapter 3: Refund Policy\nIf an application is withdrawn before biometrics, 70 percent of the fee is refunded.",
]


def _recorded() -> types.GenerateContentResponse:
    return types.GenerateContentResponse.model_validate(json.loads(FIXTURE.read_text()))


def _response(chunks: list[tuple[str, str | None, int | None]]) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                grounding_metadata=types.GroundingMetadata(
                    grounding_chunks=[
                        types.GroundingChunk(
                            retrieved_context=types.GroundingChunkRetrievedContext(
                                text=text, title=title, page_number=page
                            )
                        )
                        for text, title, page in chunks
                    ]
                )
            )
        ]
    )


async def test_recorded_response_maps_title_and_page() -> None:
    agent_id = "agt_nopageindex"
    passages = await FileSearchBackend().passages_from_response(agent_id, _recorded(), 4)
    assert len(passages) == 1
    p = passages[0]
    assert (p.file_id, p.filename, p.page_start, p.page_end, p.score) == (
        FILE_ID,
        "visa_guide.pdf",
        1,
        1,
        None,
    )
    assert "Priority service costs 480 Zorkmids" in p.text


async def test_page_index_refines_span_of_recorded_chunk() -> None:
    agent_id = "agt_withpageindex"
    pages = extract_text(make_pdf(), "application/pdf").pages  # what ingestion stores for the PDF
    payload = json.dumps({"pages": pages}).encode()
    await get_blob_store().put(pages_path(agent_id, FILE_ID), payload, "application/json")
    passages = await FileSearchBackend().passages_from_response(agent_id, _recorded(), 4)
    assert (passages[0].page_start, passages[0].page_end) == (1, 3)


async def test_unmapped_duplicate_and_capped_chunks() -> None:
    response = _response(
        [
            ("Refund text", f"{FILE_ID}__visa_guide.pdf", 3),
            ("Foreign doc", "someone_else.pdf", 1),
            ("Refund text", f"{FILE_ID}__visa_guide.pdf", 3),
            ("", f"{FILE_ID}__visa_guide.pdf", 1),
            ("Second", "kf_bbbbbbbbbbbbbbbbbbbb__faq.md", None),
            ("Third", "kf_cccccccccccccccccccc__c.md", None),
        ]
    )
    passages = await FileSearchBackend().passages_from_response("agt_x", response, 2)
    assert [(p.file_id, p.text, p.page_start) for p in passages] == [
        (FILE_ID, "Refund text", 3),
        ("kf_bbbbbbbbbbbbbbbbbbbb", "Second", None),
    ]
    empty = types.GenerateContentResponse(candidates=[types.Candidate()])
    assert await FileSearchBackend().passages_from_response("agt_x", empty, 4) == []
    assert await FileSearchBackend().passages_from_response("agt_x", types.GenerateContentResponse(), 4) == []


def test_page_index_span() -> None:
    index = PageIndex.build(PAGES)
    assert index.span("Priority service costs 480 Zorkmids", 2) == (2, 2)
    assert index.span("reduces processing to 5 working days.\nChapter 3: Refund Policy\nIf an", 2) == (2, 3)
    # wrong hint (chunk start page) is corrected when the text is found elsewhere
    assert index.span("If an application is withdrawn before biometrics", 1) == (3, 3)
    assert index.span("text that is not in the document at all", 2) == (2, 2)
    assert index.span("", 2) == (2, 2)
    assert PageIndex.build([]).span("anything", None) == (None, None)


def test_owns_store_names() -> None:
    backend = FileSearchBackend()
    assert backend.owns("fileSearchStores/topskill-agt-1")
    assert not backend.owns("local") and not backend.owns(None) and not backend.owns("")


def test_client_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    with settings_env(monkeypatch, gemini_api_key=None):
        file_search._client = None
        with pytest.raises(IndexingError) as err:
            file_search.get_client()
        assert err.value.user_message == NOT_CONFIGURED_ERROR


def test_client_is_developer_api_even_with_vertex_env(monkeypatch: pytest.MonkeyPatch) -> None:
    with settings_env(monkeypatch, gemini_api_key="test-key", google_genai_use_vertexai="true"):
        file_search._client = None
        try:
            assert file_search.get_client().vertexai is False
        finally:
            file_search._client = None


async def test_retrieve_falls_back_on_retriable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, types.GenerateContentConfig]] = []

    async def generate_content(*, model: str, contents: str, config: types.GenerateContentConfig):
        calls.append((model, config))
        if len(calls) == 1:
            raise genai_errors.ServerError(
                503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}}
            )
        return _recorded()

    fake = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    monkeypatch.setattr(file_search, "get_client", lambda: fake)
    with settings_env(
        monkeypatch, model_file_search="gemini-3.1-flash-lite", model_file_search_fallback="gemini-3.8-flash"
    ):
        passages = await FileSearchBackend().search("agt_x", "fileSearchStores/s1", "priority", 4, rev=0)
    assert [m for m, _ in calls] == ["gemini-3.1-flash-lite", "gemini-3.8-flash"]
    tool = calls[0][1].tools[0]
    assert tool.file_search.file_search_store_names == ["fileSearchStores/s1"] and tool.file_search.top_k == 4
    assert calls[0][1].thinking_config.thinking_level == types.ThinkingLevel.MINIMAL
    assert calls[1][1].thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert passages and passages[0].file_id == FILE_ID


async def test_retrieve_does_not_fall_back_on_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def generate_content(**kwargs):
        raise genai_errors.ClientError(
            400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}}
        )

    fake = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    monkeypatch.setattr(file_search, "get_client", lambda: fake)
    with pytest.raises(genai_errors.ClientError):
        await FileSearchBackend().search("agt_x", "fileSearchStores/s1", "q", 4, rev=0)
