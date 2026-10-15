"""Knowledge backend interface, file document model and upload rules (SPEC §5.1, §6.4, §10.1, D1).

Two backends implement :class:`KnowledgeBackend`:

* ``file_search`` (prod) – Gemini File Search through the Gemini Developer API (``knowledge/file_search.py``);
* ``local`` (dev/test and prod fallback) – in-Firestore chunks with a term-frequency scorer
  (``knowledge/local.py``).

The race-free store claim (per-agent lock + transaction T9), the ready-file filter and every Firestore
status change live in ``knowledge/service.py``; a backend only talks to its index.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal, Protocol

from pydantic import BaseModel

from topskill.domain import Passage, iso_z
from topskill.errors import ApiError, Codes

BackendName = Literal["file_search", "local"]
FileStatus = Literal["processing", "ready", "failed", "unreadable"]

# Accepted uploads: extension → canonical mime type (the client's content type is never trusted).
ACCEPTED_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_AGENT = 20
MAX_FILES_PER_REQUEST = 3
MAX_FILENAME_CHARS = 120
STALE_PROCESSING_SECONDS = 10 * 60
LOCAL_STORE = "local"  # the local backend needs no real store; never written to knowledge_store

STALE_ERROR = "Processing was interrupted. Please re-upload."
UNREADABLE_ERROR = "No readable text was found in this file."
UNPARSEABLE_ERROR = "This file could not be read. Please check that it is a valid document."
TOO_LARGE_ERROR = "This file is too large to index."
DELETE_ERROR = "Deleting failed. Please try again."
INDEX_ERROR = "Indexing failed. Please try again later or re-upload the file."
NOT_CONFIGURED_ERROR = "Document indexing is not configured on this server."


class KnowledgeFileDoc(BaseModel):
    """``agents/{agent_id}/knowledge/{file_id}`` (SPEC §5.1 as amended by D1)."""

    file_id: str
    filename: str
    mime: str
    size: int
    gcs_path: str
    status: FileStatus = "processing"
    error: str | None = None
    pages: int | None = None
    backend: BackendName = "local"
    fs_document_name: str | None = None  # "fileSearchStores/…/documents/…" when file_search
    chunks: int | None = None  # local backend
    created_at: datetime
    updated_at: datetime

    def to_wire(self) -> dict:
        """→ TS ``KnowledgeFile``."""
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "mime": self.mime,
            "size": self.size,
            "status": self.status,
            "error": self.error,
            "pages": self.pages,
            "created_at": iso_z(self.created_at),
            "updated_at": iso_z(self.updated_at),
        }


@dataclass(frozen=True)
class ExtractedText:
    """Text of a document split into pages.

    ``real_pages`` is True only when the pages are physical pages (PDF); for other formats the text is
    split into ~1200-char sections and page numbers are never shown to users.
    """

    pages: list[str]
    real_pages: bool

    @property
    def has_text(self) -> bool:
        return any(p.strip() for p in self.pages)

    @property
    def page_count(self) -> int | None:
        return len(self.pages) if self.real_pages else None


@dataclass(frozen=True)
class IngestResult:
    pages: int | None
    fs_document_name: str | None = None
    chunks: int | None = None


class IndexingError(Exception):
    """A backend failed to index a file; ``user_message`` is safe to show to the creator."""

    def __init__(self, message: str, *, user_message: str = INDEX_ERROR) -> None:
        super().__init__(message)
        self.user_message = user_message


class KnowledgeBackend(Protocol):
    name: BackendName
    needs_store: bool  # True when the backend keeps a real store claimed in ``knowledge_store``

    def owns(self, store: str | None) -> bool:
        """True when ``store`` (``agents/{id}.knowledge_store``) was created by this backend."""
        ...

    async def create_store(self, agent_id: str) -> str: ...

    async def delete_store(self, store: str) -> None: ...

    async def ingest(
        self, agent_id: str, store: str, file: KnowledgeFileDoc, data: bytes, text: ExtractedText
    ) -> IngestResult: ...

    async def delete(self, agent_id: str, store: str | None, file: KnowledgeFileDoc) -> None: ...

    async def search(self, agent_id: str, store: str, query: str, top_k: int, *, rev: int) -> list[Passage]:
        """Ranked passages; ``file_id`` is our ``kf_`` id (unmapped passages are dropped by the service)."""
        ...


def file_type(filename: str) -> str:
    """Canonical mime type for an accepted upload, else 400 ``unsupported_file_type``."""
    suffix = PurePosixPath(filename.lower()).suffix
    mime = ACCEPTED_TYPES.get(suffix)
    if mime is None:
        raise ApiError(
            400,
            Codes.UNSUPPORTED_FILE_TYPE,
            "Upload a PDF, DOCX, Markdown, text or CSV file.",
            {"filename": filename[:MAX_FILENAME_CHARS], "accepted": sorted(ACCEPTED_TYPES)},
        )
    return mime


def clean_filename(raw: str | None) -> str:
    """Base name only, no control characters or separators, at most ``MAX_FILENAME_CHARS`` (keeps suffix)."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '/:*?"<>|').strip().strip(".")
    if not name:
        name = "document"
    if len(name) > MAX_FILENAME_CHARS:
        suffix = PurePosixPath(name).suffix[:10]
        name = name[: MAX_FILENAME_CHARS - len(suffix)].rstrip() + suffix
    return name


def display_name(file_id: str, filename: str) -> str:
    """Name stored in the index; the ``file_id`` prefix maps results back to our file (D1)."""
    return f"{file_id}__{filename}"


def parse_display_name(title: str | None) -> tuple[str | None, str | None]:
    """``"kf_…__visa_guide.pdf"`` → ``("kf_…", "visa_guide.pdf")``; anything else → ``(None, title)``."""
    if not title:
        return None, None
    file_id, sep, filename = title.partition("__")
    if not sep or not file_id.startswith("kf_"):
        return None, title
    return file_id, filename
