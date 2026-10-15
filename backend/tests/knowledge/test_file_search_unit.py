"""File Search backend without network: response mapping (recorded fixture), page spans, model fallback,
upload shape and retry policy, deletion."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors
from google.genai import types

from tests.core.helpers import settings_env
from tests.knowledge.docs import make_pdf
from topskill.db import utcnow
from topskill.knowledge import file_search
from topskill.knowledge.base import (
    NOT_CONFIGURED_ERROR,
    ExtractedText,
    IndexingError,
    KnowledgeFileDoc,
    file_type,
)
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


def _read(path: str) -> bytes:
    return Path(path).read_bytes()


def _exists(path: str) -> bool:
    return os.path.exists(path)


class FakeUploads:
    """Records upload calls; ``failures`` are raised by the first calls, in order."""

    def __init__(self, failures: list[BaseException] | None = None, *, done: bool = True) -> None:
        self.failures = list(failures or [])
        self.calls: list[dict] = []
        self.done = done
        self.deleted: list[str] = []
        self.documents: list[SimpleNamespace] = []

    async def upload_to_file_search_store(self, *, file, file_search_store_name, config):
        assert isinstance(file, str), "uploads go through a temp file path (D1)"
        body = _read(file)
        self.calls.append({"file": file, "body": body, "store": file_search_store_name, "config": config})
        if self.failures:
            raise self.failures.pop(0)
        return SimpleNamespace(
            done=self.done,
            error=None,
            response=SimpleNamespace(document_name=f"{file_search_store_name}/documents/d1"),
        )

    async def get_operation(self, op):
        return op

    async def delete_document(self, *, name, config):
        self.deleted.append(name)

    async def list_documents(self, *, parent):
        async def pager():
            for d in self.documents:
                yield d

        return pager()

    def client(self) -> SimpleNamespace:
        return SimpleNamespace(
            aio=SimpleNamespace(
                file_search_stores=SimpleNamespace(
                    upload_to_file_search_store=self.upload_to_file_search_store,
                    documents=SimpleNamespace(delete=self.delete_document, list=self.list_documents),
                ),
                operations=SimpleNamespace(get=self.get_operation),
            )
        )


def _doc(filename: str, *, fs_document_name: str | None = None) -> KnowledgeFileDoc:
    now = utcnow()
    return KnowledgeFileDoc(
        file_id=FILE_ID,
        filename=filename,
        mime=file_type(filename),
        size=3,
        gcs_path=f"knowledge/agt_x/{FILE_ID}/{filename}",
        backend="file_search",
        fs_document_name=fs_document_name,
        created_at=now,
        updated_at=now,
    )


def _client_error(code: int, status: str) -> genai_errors.ClientError:
    return genai_errors.ClientError(code, {"error": {"code": code, "message": "x", "status": status}})


@pytest.mark.parametrize("filename", ["notes.csv", "memo.docx", "a.md", "b.txt", "c.pdf"])
async def test_upload_uses_temp_path_with_suffix_and_no_mime_type(monkeypatch, filename) -> None:
    fake = FakeUploads()
    monkeypatch.setattr(file_search, "get_client", fake.client)
    text = ExtractedText(pages=["hello"], real_pages=False)
    result = await FileSearchBackend().ingest("agt_x", "fileSearchStores/s1", _doc(filename), b"a,b", text)
    assert result.fs_document_name == "fileSearchStores/s1/documents/d1"
    [call] = fake.calls
    assert call["file"].endswith(PurePosixPath(filename).suffix) and call["body"] == b"a,b"
    assert "mime_type" not in call["config"]
    assert call["config"]["display_name"] == f"{FILE_ID}__{filename}"
    assert call["config"]["chunking_config"]["white_space_config"]["max_tokens_per_chunk"] == 160
    assert not _exists(call["file"])  # temp file removed


async def test_upload_client_error_is_not_retried(monkeypatch) -> None:
    fake = FakeUploads([_client_error(400, "INVALID_ARGUMENT")])
    monkeypatch.setattr(file_search, "get_client", fake.client)
    monkeypatch.setattr(file_search, "UPLOAD_RETRY_DELAY_S", 0)
    text = ExtractedText(pages=["hello"], real_pages=False)
    with pytest.raises(IndexingError):
        await FileSearchBackend().ingest("agt_x", "fileSearchStores/s1", _doc("a.csv"), b"x", text)
    assert len(fake.calls) == 1 and not _exists(fake.calls[0]["file"])


async def test_upload_server_error_is_retried_once(monkeypatch) -> None:
    busy = genai_errors.ServerError(503, {"error": {"code": 503, "message": "x", "status": "UNAVAILABLE"}})
    fake = FakeUploads([busy])
    monkeypatch.setattr(file_search, "get_client", fake.client)
    monkeypatch.setattr(file_search, "UPLOAD_RETRY_DELAY_S", 0)
    text = ExtractedText(pages=["hello"], real_pages=False)
    result = await FileSearchBackend().ingest("agt_x", "fileSearchStores/s1", _doc("a.txt"), b"x", text)
    assert len(fake.calls) == 2 and result.fs_document_name


async def test_upload_poll_timeout_is_not_retried(monkeypatch) -> None:
    fake = FakeUploads(done=False)
    monkeypatch.setattr(file_search, "get_client", fake.client)
    monkeypatch.setattr(file_search, "UPLOAD_TIMEOUT_S", 0.0)
    monkeypatch.setattr(file_search, "POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(file_search, "UPLOAD_RETRY_DELAY_S", 0)
    text = ExtractedText(pages=["hello"], real_pages=False)
    with pytest.raises(file_search.UploadTimeout):
        await FileSearchBackend().ingest("agt_x", "fileSearchStores/s1", _doc("a.txt"), b"x", text)
    assert len(fake.calls) == 1


async def test_delete_removes_document_and_leftovers_of_the_same_file(monkeypatch) -> None:
    fake = FakeUploads()
    fake.documents = [
        SimpleNamespace(name="fileSearchStores/s1/documents/d1", display_name=f"{FILE_ID}__a.txt"),
        SimpleNamespace(name="fileSearchStores/s1/documents/old", display_name=f"{FILE_ID}__a.txt"),
        SimpleNamespace(name="fileSearchStores/s1/documents/other", display_name="kf_other__b.txt"),
    ]
    monkeypatch.setattr(file_search, "get_client", fake.client)
    doc = _doc("a.txt", fs_document_name="fileSearchStores/s1/documents/d1")
    await FileSearchBackend().delete("agt_x", "fileSearchStores/s1", doc)
    assert fake.deleted == ["fileSearchStores/s1/documents/d1", "fileSearchStores/s1/documents/old"]


async def test_delete_raises_when_the_document_cannot_be_deleted(monkeypatch) -> None:
    fake = FakeUploads()

    async def failing_delete(*, name, config):
        raise genai_errors.ServerError(503, {"error": {"code": 503, "message": "x", "status": "UNAVAILABLE"}})

    client = fake.client()
    client.aio.file_search_stores.documents.delete = failing_delete
    monkeypatch.setattr(file_search, "get_client", lambda: client)
    with pytest.raises(genai_errors.ServerError):
        await FileSearchBackend().delete(
            "agt_x", "fileSearchStores/s1", _doc("a.txt", fs_document_name="x/d")
        )
