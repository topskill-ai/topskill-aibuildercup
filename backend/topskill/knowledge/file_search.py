"""``file_search`` knowledge backend: Gemini File Search through the Gemini Developer API (SPEC D1).

* One File Search store per agent (``display_name = "topskill-{agent_id}"``); its name is kept in
  ``agents/{id}.knowledge_store`` by the service (race-free claim, transaction T9).
* Upload: ``upload_to_file_search_store`` from a temp file path whose suffix is the original extension
  (D1; the SDK derives the content type from it; an explicit ``mime_type`` is rejected by the API for
  csv/docx), with ``display_name = "{file_id}__{filename}"`` and small white-space chunks (so a passage
  rarely spans pages), then poll the operation until done. The returned document name is saved as
  ``fs_document_name``. A 4xx answer or a poll timeout is never retried (a timed-out operation may still
  complete, so a second upload could leave an untracked document); ``delete`` also removes any document
  whose display name carries the file id, so such leftovers are dropped when the file is deleted.
* Search: ADK chat runs on Vertex, where the FileSearch tool does not exist, so retrieval is one
  Developer-API ``generate_content`` call with the FileSearch tool. The generated text is ignored; the
  passages come from ``candidates[0].grounding_metadata.grounding_chunks[*].retrieved_context``
  (verified shape: ``{text, title, file_search_store, page_number}``). ``title`` maps back to our
  ``kf_`` id. ``page_number`` is the page where the chunk starts; the exact span is refined against our
  own per-page text extraction (saved next to the original as ``pages.json``) when the chunk text can be
  located there.

Only this module uses the Developer API key (``GEMINI_API_KEY``); everything else stays on Vertex/ADC.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import PurePosixPath

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from topskill.config import get_settings
from topskill.domain import Passage
from topskill.errors import ApiError
from topskill.gemini import is_retriable, thinking_config
from topskill.knowledge.base import (
    NOT_CONFIGURED_ERROR,
    BackendName,
    ExtractedText,
    IndexingError,
    IngestResult,
    KnowledgeFileDoc,
    display_name,
    parse_display_name,
)
from topskill.storage import get_blob_store

log = logging.getLogger(__name__)

STORE_PREFIX = "fileSearchStores/"
CHUNK_TOKENS = 160
CHUNK_OVERLAP_TOKENS = 16
UPLOAD_TIMEOUT_S = 300.0
POLL_INTERVAL_S = 2.0
UPLOAD_RETRY_DELAY_S = 5.0
HTTP_TIMEOUT_MS = 120_000
PAGE_INDEX_CACHE = 64

RETRIEVAL_PROMPT = (
    "Search the documents for information relevant to the query below and quote the relevant passages "
    "verbatim. If nothing in the documents is relevant, say so.\n\nQuery: {query}"
)

_client: genai.Client | None = None
_client_key: str | None = None
_client_lock = threading.Lock()


def get_client() -> genai.Client:
    """Developer-API client (API key from ``GEMINI_API_KEY``), module singleton."""
    global _client, _client_key
    secret = get_settings().gemini_api_key
    key = secret.get_secret_value().strip() if secret else ""
    if not key:
        raise IndexingError("GEMINI_API_KEY is not set", user_message=NOT_CONFIGURED_ERROR)
    with _client_lock:
        if _client is None or _client_key != key:
            _client = genai.Client(
                vertexai=False,  # GOOGLE_GENAI_USE_VERTEXAI=true is set for the rest of the app
                api_key=key,
                http_options=types.HttpOptions(timeout=HTTP_TIMEOUT_MS),
            )
            _client_key = key
        return _client


def pages_path(agent_id: str, file_id: str) -> str:
    return f"knowledge/{agent_id}/{file_id}/pages.json"


# --------------------------------------------------------------------------- page span refinement

_NORM_RE = re.compile(r"[\W_]+")
_PROBE_CHARS = 40


def _norm(text: str) -> str:
    return _NORM_RE.sub("", text.lower())


@dataclass(frozen=True)
class PageIndex:
    """Normalised text of every page concatenated, with the start offset of each page."""

    text: str
    starts: list[int]

    @classmethod
    def build(cls, pages: list[str]) -> PageIndex:
        starts: list[int] = []
        parts: list[str] = []
        offset = 0
        for page in pages:
            starts.append(offset)
            norm = _norm(page)
            parts.append(norm)
            offset += len(norm)
        return cls(text="".join(parts), starts=starts)

    def page_at(self, pos: int) -> int:
        """1-based page containing normalised offset ``pos``."""
        page = 1
        for i, start in enumerate(self.starts):
            if start <= pos:
                page = i + 1
            else:
                break
        return page

    def span(self, chunk_text: str, hint_page: int | None) -> tuple[int | None, int | None]:
        """``(page_start, page_end)`` of ``chunk_text``; falls back to the hint when it cannot be located."""
        norm = _norm(chunk_text)
        if not norm or not self.text:
            return hint_page, hint_page
        n_pages = len(self.starts)
        hint = hint_page if hint_page and 1 <= hint_page <= n_pages else None
        origin = max(self.starts[hint - 1] - _PROBE_CHARS, 0) if hint else 0
        head = norm[:_PROBE_CHARS]
        tail = norm[-_PROBE_CHARS:]
        head_pos = self.text.find(head, origin)
        if head_pos < 0 and hint:
            head_pos = self.text.find(head)
        start = self.page_at(head_pos) if head_pos >= 0 else hint
        tail_from = head_pos if head_pos >= 0 else (self.starts[hint - 1] if hint else 0)
        tail_pos = self.text.find(tail, tail_from)
        end = self.page_at(tail_pos + len(tail) - 1) if tail_pos >= 0 else start
        if start is None:
            return hint_page, hint_page
        if end is None or end < start:
            end = start
        return start, end


def _write_temp(data: bytes, suffix: str) -> str:
    """Write ``data`` to a temp file named with the original extension (D1); the caller unlinks it."""
    with tempfile.NamedTemporaryFile(prefix="topskill-kf-", suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        return tmp.name


class UploadTimeout(IndexingError):
    """The indexing operation did not finish in time (it may still complete later)."""


def _retriable_upload_error(exc: genai_errors.APIError) -> bool:
    """Server errors and 429 are retried once; other 4xx answers can never succeed."""
    return not isinstance(exc, genai_errors.ClientError) or exc.code in (408, 429)


class FileSearchBackend:
    name: BackendName = "file_search"
    needs_store = True

    def __init__(self) -> None:
        self._pages: OrderedDict[str, PageIndex | None] = OrderedDict()

    def owns(self, store: str | None) -> bool:
        return bool(store) and store.startswith(STORE_PREFIX)

    async def create_store(self, agent_id: str) -> str:
        client = get_client()
        try:
            store = await client.aio.file_search_stores.create(
                config={"display_name": f"topskill-{agent_id}"}
            )
        except genai_errors.APIError as exc:
            raise IndexingError(f"create store failed: {exc.code} {exc.status}") from exc
        if not store.name:
            raise IndexingError("create store returned no name")
        log.info("file_search_store_created agent_id=%s store=%s", agent_id, store.name)
        return store.name

    async def delete_store(self, store: str) -> None:
        try:
            await get_client().aio.file_search_stores.delete(name=store, config={"force": True})
            log.info("file_search_store_deleted store=%s", store)
        except genai_errors.APIError as exc:
            if exc.code != 404:
                raise

    async def _upload_once(self, store: str, file: KnowledgeFileDoc, data: bytes) -> str:
        client = get_client()
        path = await asyncio.to_thread(_write_temp, data, PurePosixPath(file.filename).suffix.lower())
        try:
            op = await client.aio.file_search_stores.upload_to_file_search_store(
                file=path,  # no mime_type: the API rejects it for csv/docx; the SDK guesses it
                file_search_store_name=store,
                config={
                    "display_name": display_name(file.file_id, file.filename),
                    "chunking_config": {
                        "white_space_config": {
                            "max_tokens_per_chunk": CHUNK_TOKENS,
                            "max_overlap_tokens": CHUNK_OVERLAP_TOKENS,
                        }
                    },
                },
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                log.warning("file_search_tmp_unlink_failed path=%s", path)
        deadline = time.monotonic() + UPLOAD_TIMEOUT_S
        while not op.done:
            if time.monotonic() > deadline:
                raise UploadTimeout(f"indexing timed out after {UPLOAD_TIMEOUT_S:.0f}s")
            await asyncio.sleep(POLL_INTERVAL_S)
            op = await client.aio.operations.get(op)
        if op.error:
            raise IndexingError(f"indexing operation failed: {op.error}")
        document_name = op.response.document_name if op.response else None
        if not document_name:
            raise IndexingError("indexing operation returned no document name")
        return document_name

    async def ingest(
        self, agent_id: str, store: str, file: KnowledgeFileDoc, data: bytes, text: ExtractedText
    ) -> IngestResult:
        if text.real_pages:  # our own page text, used to refine page spans at search time
            payload = json.dumps({"pages": text.pages}, ensure_ascii=False).encode()
            await get_blob_store().put(pages_path(agent_id, file.file_id), payload, "application/json")
        document_name = ""
        for attempt in (1, 2):  # the first upload into a fresh store occasionally fails; retry once
            try:
                document_name = await self._upload_once(store, file, data)
                break
            except UploadTimeout:
                raise  # the operation may still complete: a second upload could duplicate the document
            except IndexingError as exc:
                if exc.user_message == NOT_CONFIGURED_ERROR or attempt == 2:
                    raise
                reason = str(exc)
            except genai_errors.APIError as exc:
                if attempt == 2 or not _retriable_upload_error(exc):
                    raise IndexingError(f"upload failed: {exc.code} {exc.status}") from exc
                reason = f"{exc.code} {exc.status}"
            log.warning("file_search_upload_retry file_id=%s reason=%s", file.file_id, reason)
            await asyncio.sleep(UPLOAD_RETRY_DELAY_S)
        log.info("file_search_document_ready file_id=%s document=%s", file.file_id, document_name)
        return IngestResult(pages=text.page_count, fs_document_name=document_name)

    async def delete(self, agent_id: str, store: str | None, file: KnowledgeFileDoc) -> None:
        """Remove the file's document. Raises when that fails, so the caller keeps the reference.

        Also removes, best effort, any other document of the file (``{file_id}__`` display name) that a
        timed-out or failed upload may have left behind without a recorded ``fs_document_name``.
        """
        self._pages.pop(file.file_id, None)
        if file.fs_document_name:
            await self._delete_document(file.fs_document_name)
        if store and self.owns(store):
            await self._delete_leftovers(store, file)

    async def _delete_document(self, name: str) -> None:
        try:
            await get_client().aio.file_search_stores.documents.delete(name=name, config={"force": True})
        except genai_errors.APIError as exc:
            if exc.code != 404:
                raise

    async def _delete_leftovers(self, store: str, file: KnowledgeFileDoc) -> None:
        prefix = display_name(file.file_id, "")
        try:
            pager = await get_client().aio.file_search_stores.documents.list(parent=store)
            names = [
                d.name
                async for d in pager
                if d.name and d.name != file.fs_document_name and (d.display_name or "").startswith(prefix)
            ]
            for name in names:
                await self._delete_document(name)
                log.info("file_search_leftover_deleted file_id=%s document=%s", file.file_id, name)
        except Exception:
            log.warning("file_search_leftover_cleanup_failed file_id=%s", file.file_id, exc_info=True)

    async def _page_index(self, agent_id: str, file_id: str) -> PageIndex | None:
        if file_id in self._pages:
            self._pages.move_to_end(file_id)
            return self._pages[file_id]
        index: PageIndex | None = None
        try:
            raw = await get_blob_store().get(pages_path(agent_id, file_id))
            pages = json.loads(raw).get("pages") or []
            index = PageIndex.build([str(p) for p in pages]) if pages else None
        except ApiError:
            index = None  # non-PDF files have no page index
        except (ValueError, TypeError):
            log.warning("file_search_page_index_invalid file_id=%s", file_id)
        self._pages[file_id] = index
        while len(self._pages) > PAGE_INDEX_CACHE:
            self._pages.popitem(last=False)
        return index

    async def _retrieve(self, store: str, query: str, top_k: int) -> types.GenerateContentResponse:
        settings = get_settings()
        models = (settings.model_file_search, settings.model_file_search_fallback)
        chain = list(dict.fromkeys(m for m in models if m))
        client = get_client()
        last: BaseException | None = None
        for model in chain:
            level = "minimal" if "flash-lite" in model else "low"
            tool = types.Tool(file_search=types.FileSearch(file_search_store_names=[store], top_k=top_k))
            config = types.GenerateContentConfig(
                tools=[tool],
                thinking_config=thinking_config(model, level),
            )
            try:
                return await client.aio.models.generate_content(
                    model=model, contents=RETRIEVAL_PROMPT.format(query=query), config=config
                )
            except Exception as exc:
                last = exc
                if not is_retriable(exc):
                    raise
                log.warning("file_search_model_fallback model=%s error=%s", model, type(exc).__name__)
        assert last is not None
        raise last

    async def search(self, agent_id: str, store: str, query: str, top_k: int, *, rev: int) -> list[Passage]:
        response = await self._retrieve(store, query, top_k)
        return await self.passages_from_response(agent_id, response, top_k)

    async def passages_from_response(
        self, agent_id: str, response: types.GenerateContentResponse, top_k: int
    ) -> list[Passage]:
        """Map ``grounding_chunks[*].retrieved_context`` → ``Passage`` (order kept, duplicates dropped)."""
        candidates = response.candidates or []
        gm = candidates[0].grounding_metadata if candidates else None
        passages: list[Passage] = []
        seen: set[tuple[str, int | None, str]] = set()
        for chunk in (gm.grounding_chunks if gm else None) or []:
            ctx = chunk.retrieved_context
            if ctx is None or not ctx.text:
                continue
            file_id, filename = parse_display_name(ctx.title)
            if not file_id:
                continue
            page_start = page_end = ctx.page_number
            index = await self._page_index(agent_id, file_id)
            if index is not None:
                page_start, page_end = index.span(ctx.text, ctx.page_number)
            key = (file_id, page_start, ctx.text[:80])
            if key in seen:
                continue
            seen.add(key)
            passages.append(
                Passage(
                    file_id=file_id,
                    filename=filename or "",
                    text=ctx.text.strip(),
                    page_start=page_start,
                    page_end=page_end,
                    score=None,
                )
            )
            if len(passages) >= top_k:
                break
        return passages
