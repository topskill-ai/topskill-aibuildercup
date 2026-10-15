"""Knowledge service: uploads, background ingestion, store claim, stale sweep, search (SPEC §10.1, D1).

Rules implemented here:

* ``ensure_store`` is race-free: an in-process ``asyncio.Lock`` per agent, then transaction T9 writes
  ``agents/{id}.knowledge_store`` only while it is still unset; a store created by a losing writer is
  deleted again.
* Every file status change (→ ready / failed / unreadable) and every deletion bumps
  ``agents/{id}.knowledge_rev`` in the same batch as the file write (never touches ``updated_at``).
* Ingestion runs in the background via ``tasks.spawn`` and never raises.
* Files left ``processing`` for more than 10 minutes (e.g. after a revision rollout) are marked failed
  before every listing, so polling always terminates.
* Knowledge applies live: search covers every ``ready`` file of the agent indexed by the active backend.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from google.api_core import exceptions as gexc
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from topskill import tasks
from topskill.config import get_settings
from topskill.db import Col, get_db, new_id, utcnow
from topskill.domain import AgentDoc, Passage
from topskill.errors import ApiError, Codes
from topskill.knowledge.base import (
    INDEX_ERROR,
    MAX_FILE_BYTES,
    MAX_FILES_PER_AGENT,
    MAX_FILES_PER_REQUEST,
    STALE_ERROR,
    STALE_PROCESSING_SECONDS,
    UNPARSEABLE_ERROR,
    UNREADABLE_ERROR,
    BackendName,
    IndexingError,
    KnowledgeBackend,
    KnowledgeFileDoc,
    clean_filename,
    file_type,
)
from topskill.knowledge.extract import UnparseableFile, extract_text
from topskill.storage import get_blob_store

log = logging.getLogger(__name__)

FILE_ID_RE = re.compile(r"^kf_[0-9a-f]{20}$")
MAX_QUERY_CHARS = 500
MAX_CONCURRENT_INGESTIONS = 3
READY_CACHE_TTL_S = 60.0
MISSING_UPLOAD_ERROR = "The uploaded file is missing. Please re-upload."

_backends: dict[str, KnowledgeBackend] = {}
_store_locks: dict[str, asyncio.Lock] = {}
_upload_locks: dict[str, asyncio.Lock] = {}
_ingest_slots: asyncio.Semaphore | None = None


# --------------------------------------------------------------------------- backends


def backend_for(name: BackendName | str) -> KnowledgeBackend:
    """The backend instance for ``name`` (one per process; they hold small caches)."""
    backend = _backends.get(name)
    if backend is None:
        if name == "file_search":
            from topskill.knowledge.file_search import FileSearchBackend

            backend = FileSearchBackend()
        elif name == "local":
            from topskill.knowledge.local import LocalBackend

            backend = LocalBackend()
        else:
            raise ValueError(f"unknown knowledge backend: {name}")
        _backends[name] = backend
    return backend


def get_backend() -> KnowledgeBackend:
    """The active backend (``KNOWLEDGE_BACKEND``)."""
    return backend_for(get_settings().knowledge_backend)


def backend_name() -> BackendName:
    return get_settings().knowledge_backend


# --------------------------------------------------------------------------- references


def _agent_ref(agent_id: str):
    return get_db().collection(Col.AGENTS).document(agent_id)


def _files_col(agent_id: str):
    return _agent_ref(agent_id).collection(Col.KNOWLEDGE)


def blob_path(agent_id: str, file_id: str, filename: str) -> str:
    return f"knowledge/{agent_id}/{file_id}/{filename}"


def _lock(registry: dict[str, asyncio.Lock], key: str) -> asyncio.Lock:
    lock = registry.get(key)
    if lock is None:
        lock = registry[key] = asyncio.Lock()
    return lock


def _not_found() -> ApiError:
    return ApiError(404, Codes.NOT_FOUND, "This knowledge file does not exist.")


async def get_file(agent_id: str, file_id: str) -> KnowledgeFileDoc | None:
    if not FILE_ID_RE.match(file_id or ""):
        return None
    snap = await _files_col(agent_id).document(file_id).get()
    return KnowledgeFileDoc.model_validate(snap.to_dict()) if snap.exists else None


async def _all_files(agent_id: str) -> list[KnowledgeFileDoc]:
    files: list[KnowledgeFileDoc] = []
    async for snap in _files_col(agent_id).stream():
        try:
            files.append(KnowledgeFileDoc.model_validate(snap.to_dict()))
        except ValueError:
            log.warning("knowledge_file_invalid agent_id=%s file_id=%s", agent_id, snap.id)
    files.sort(key=lambda f: (f.created_at, f.file_id))
    return files


# --------------------------------------------------------------------------- status writes


async def _set_status(agent_id: str, file_id: str, fields: dict[str, Any]) -> bool:
    """Update the file doc and bump ``knowledge_rev`` atomically. False when the file no longer exists."""
    db = get_db()
    batch = db.batch()
    batch.update(_files_col(agent_id).document(file_id), {**fields, "updated_at": utcnow()})
    batch.update(_agent_ref(agent_id), {"knowledge_rev": firestore.Increment(1)})
    try:
        await batch.commit()
    except gexc.NotFound:
        return False
    _ready_cache.pop(agent_id, None)
    return True


# --------------------------------------------------------------------------- store claim (T9)


async def _claim_store(agent_id: str, store: str, backend: KnowledgeBackend) -> str:
    """Transaction T9: set ``knowledge_store`` only while it is unset (or not this backend's); → winner."""
    db = get_db()
    ref = _agent_ref(agent_id)

    @firestore.async_transactional
    async def _claim(tx: Any) -> str:
        snap = await ref.get(transaction=tx)
        if not snap.exists:
            raise ApiError(404, Codes.AGENT_NOT_FOUND, "This agent does not exist.")
        current = (snap.to_dict() or {}).get("knowledge_store")
        if current and backend.owns(current):
            return current
        tx.update(ref, {"knowledge_store": store})
        return store

    return await _claim(db.transaction(max_attempts=10))


async def ensure_store(agent_id: str) -> str:
    """The agent's store name, created once (per-agent lock + T9; a losing store is deleted)."""
    backend = get_backend()
    async with _lock(_store_locks, agent_id):
        snap = await _agent_ref(agent_id).get()
        if not snap.exists:
            raise ApiError(404, Codes.AGENT_NOT_FOUND, "This agent does not exist.")
        current = (snap.to_dict() or {}).get("knowledge_store")
        if current and backend.owns(current):
            return current
        created = await backend.create_store(agent_id)
        try:
            winner = await _claim_store(agent_id, created, backend)
        except BaseException:
            await _discard_store(backend, created)
            raise
        if winner != created:
            log.info("knowledge_store_lost_race agent_id=%s winner=%s", agent_id, winner)
            await _discard_store(backend, created)
        return winner


async def _discard_store(backend: KnowledgeBackend, store: str) -> None:
    try:
        await backend.delete_store(store)
    except Exception:
        log.warning("knowledge_store_delete_failed store=%s", store, exc_info=True)


# --------------------------------------------------------------------------- uploads


@dataclass(frozen=True)
class Upload:
    filename: str
    data: bytes


def validate_upload(filename: str | None, data: bytes) -> tuple[str, str]:
    """→ ``(clean filename, mime)``; 400 ``unsupported_file_type`` / 413 ``payload_too_large`` / 400 empty."""
    name = clean_filename(filename)
    mime = file_type(name)
    if len(data) > MAX_FILE_BYTES:
        raise ApiError(
            413,
            Codes.PAYLOAD_TOO_LARGE,
            "Each file must be 10 MB or smaller.",
            {"filename": name, "max_bytes": MAX_FILE_BYTES},
        )
    if not data:
        raise ApiError(400, Codes.VALIDATION_ERROR, "The file is empty.", {"filename": name})
    return name, mime


async def add_files(agent: AgentDoc, uploads: list[Upload]) -> list[KnowledgeFileDoc]:
    """Store originals, create ``processing`` docs and start one ingestion task per file."""
    if not 1 <= len(uploads) <= MAX_FILES_PER_REQUEST:
        raise ApiError(
            400,
            Codes.VALIDATION_ERROR,
            f"Upload between 1 and {MAX_FILES_PER_REQUEST} files per request.",
            {"max_files_per_request": MAX_FILES_PER_REQUEST},
        )
    checked = [(*validate_upload(u.filename, u.data), u.data) for u in uploads]
    store = get_blob_store()
    created: list[KnowledgeFileDoc] = []
    async with _lock(_upload_locks, agent.id):
        existing = len(await _all_files(agent.id))
        if existing + len(checked) > MAX_FILES_PER_AGENT:
            raise ApiError(
                400,
                Codes.KNOWLEDGE_LIMIT_REACHED,
                f"An agent can have at most {MAX_FILES_PER_AGENT} knowledge files.",
                {"max_files": MAX_FILES_PER_AGENT, "current": existing},
            )
        for name, mime, data in checked:
            file_id = new_id("kf")
            path = blob_path(agent.id, file_id, name)
            await store.put(path, data, mime)
            now = utcnow()
            doc = KnowledgeFileDoc(
                file_id=file_id,
                filename=name,
                mime=mime,
                size=len(data),
                gcs_path=path,
                status="processing",
                backend=backend_name(),
                created_at=now,
                updated_at=now,
            )
            await _files_col(agent.id).document(file_id).create(doc.model_dump(mode="python"))
            created.append(doc)
    for doc in created:
        tasks.spawn(ingest_file(agent.id, doc.file_id), name=f"ingest:{doc.file_id}")
    log.info("knowledge_upload agent_id=%s files=%s", agent.id, [d.file_id for d in created])
    return created


# --------------------------------------------------------------------------- ingestion


def _slots() -> asyncio.Semaphore:
    global _ingest_slots
    if _ingest_slots is None:
        _ingest_slots = asyncio.Semaphore(MAX_CONCURRENT_INGESTIONS)
    return _ingest_slots


async def ingest_file(agent_id: str, file_id: str) -> None:
    """Background body of an upload (SPEC §10.1). Never raises."""
    try:
        async with _slots():
            await _ingest(agent_id, file_id)
    except Exception:
        log.exception("knowledge_ingest_crashed agent_id=%s file_id=%s", agent_id, file_id)
        try:
            await _set_status(agent_id, file_id, {"status": "failed", "error": INDEX_ERROR})
        except Exception:
            log.exception("knowledge_ingest_fail_status_failed file_id=%s", file_id)


async def _ingest(agent_id: str, file_id: str) -> None:
    started = time.monotonic()
    doc = await get_file(agent_id, file_id)
    if doc is None or doc.status != "processing":
        return
    try:
        data = await get_blob_store().get(doc.gcs_path)
    except ApiError:
        await _set_status(agent_id, file_id, {"status": "failed", "error": MISSING_UPLOAD_ERROR})
        return

    try:
        text = await asyncio.to_thread(extract_text, data, doc.mime)
    except UnparseableFile as exc:
        log.info("knowledge_unparseable file_id=%s reason=%s", file_id, exc)
        await _set_status(agent_id, file_id, {"status": "unreadable", "error": UNPARSEABLE_ERROR})
        return
    if not text.has_text:
        await _set_status(
            agent_id,
            file_id,
            {"status": "unreadable", "error": UNREADABLE_ERROR, "pages": text.page_count},
        )
        return

    backend = get_backend()
    try:
        store = await ensure_store(agent_id)
        result = await backend.ingest(agent_id, store, doc, data, text)
    except IndexingError as exc:
        log.warning("knowledge_ingest_failed file_id=%s backend=%s error=%s", file_id, backend.name, exc)
        await _cleanup_index(backend, agent_id, None, doc)
        await _set_status(agent_id, file_id, {"status": "failed", "error": exc.user_message})
        return
    except Exception:
        log.exception("knowledge_ingest_error file_id=%s backend=%s", file_id, backend.name)
        await _cleanup_index(backend, agent_id, None, doc)
        await _set_status(agent_id, file_id, {"status": "failed", "error": INDEX_ERROR})
        return

    fields = {
        "status": "ready",
        "error": None,
        "pages": result.pages,
        "backend": backend.name,
        "fs_document_name": result.fs_document_name,
        "chunks": result.chunks,
    }
    if not await _set_status(agent_id, file_id, fields):  # deleted while indexing: drop what we indexed
        indexed = doc.model_copy(update={"fs_document_name": result.fs_document_name})
        await _cleanup_index(backend, agent_id, store, indexed)
        return
    log.info(
        "knowledge_ready agent_id=%s file_id=%s backend=%s pages=%s took_ms=%d",
        agent_id,
        file_id,
        backend.name,
        result.pages,
        int((time.monotonic() - started) * 1000),
    )


async def _cleanup_index(
    backend: KnowledgeBackend, agent_id: str, store: str | None, doc: KnowledgeFileDoc
) -> None:
    try:
        await backend.delete(agent_id, store, doc)
    except Exception:
        log.warning("knowledge_index_cleanup_failed file_id=%s", doc.file_id, exc_info=True)


# --------------------------------------------------------------------------- listing and stale sweep


def _is_stale(doc: KnowledgeFileDoc, now: Any) -> bool:
    return doc.status == "processing" and doc.updated_at < now - timedelta(seconds=STALE_PROCESSING_SECONDS)


async def _mark_stale(agent_id: str, file_id: str) -> None:
    if await _set_status(agent_id, file_id, {"status": "failed", "error": STALE_ERROR}):
        log.info("knowledge_marked_stale agent_id=%s file_id=%s", agent_id, file_id)


async def list_files(agent_id: str) -> list[KnowledgeFileDoc]:
    """All files of the agent (upload order); stale ``processing`` files are marked failed first."""
    files = await _all_files(agent_id)
    now = utcnow()
    stale = [f for f in files if _is_stale(f, now)]
    if not stale:
        return files
    for f in stale:
        await _mark_stale(agent_id, f.file_id)
    return await _all_files(agent_id)


async def sweep_stale(agent_id: str) -> int:
    """Mark this agent's stale ``processing`` files failed; → count."""
    now = utcnow()
    count = 0
    async for snap in _files_col(agent_id).where(filter=FieldFilter("status", "==", "processing")).stream():
        doc = KnowledgeFileDoc.model_validate(snap.to_dict())
        if _is_stale(doc, now):
            await _mark_stale(agent_id, doc.file_id)
            count += 1
    return count


async def sweep_stale_all() -> int:
    """Startup sweep over every agent (collection-group query on ``knowledge.status``). Never raises."""
    now = utcnow()
    count = 0
    try:
        query = (
            get_db().collection_group(Col.KNOWLEDGE).where(filter=FieldFilter("status", "==", "processing"))
        )
        async for snap in query.stream():
            agent_ref = snap.reference.parent.parent
            if agent_ref is None:
                continue
            doc = KnowledgeFileDoc.model_validate(snap.to_dict())
            if _is_stale(doc, now):
                await _mark_stale(agent_ref.id, doc.file_id)
                count += 1
    except Exception:
        log.warning("knowledge_startup_sweep_failed", exc_info=True)
    return count


async def counts(agent_id: str) -> dict[str, int]:
    """``{files, ready, processing, failed}`` for ``Agent.knowledge`` (unreadable counts as failed)."""
    files = await _all_files(agent_id)
    return {
        "files": len(files),
        "ready": sum(1 for f in files if f.status == "ready"),
        "processing": sum(1 for f in files if f.status == "processing"),
        "failed": sum(1 for f in files if f.status in ("failed", "unreadable")),
    }


# --------------------------------------------------------------------------- deletion


async def delete_file(agent_id: str, file_id: str) -> None:
    """Remove a file from the index, storage and Firestore; bumps ``knowledge_rev``. 404 when missing."""
    doc = await get_file(agent_id, file_id)
    if doc is None:
        raise _not_found()
    agent_snap = await _agent_ref(agent_id).get()
    store = (agent_snap.to_dict() or {}).get("knowledge_store") if agent_snap.exists else None
    await _cleanup_index(backend_for(doc.backend), agent_id, store, doc)
    try:
        await get_blob_store().delete_prefix(f"knowledge/{agent_id}/{file_id}/")
    except Exception:
        log.warning("knowledge_blob_delete_failed file_id=%s", file_id, exc_info=True)
    db = get_db()
    batch = db.batch()
    batch.delete(_files_col(agent_id).document(file_id))
    batch.update(_agent_ref(agent_id), {"knowledge_rev": firestore.Increment(1)})
    await batch.commit()
    _ready_cache.pop(agent_id, None)
    log.info("knowledge_deleted agent_id=%s file_id=%s", agent_id, file_id)


# --------------------------------------------------------------------------- search


@dataclass
class _ReadyEntry:
    rev: int
    expires: float
    files: dict[str, KnowledgeFileDoc]


_ready_cache: dict[str, _ReadyEntry] = {}


async def _ready_files(agent_id: str, rev: int) -> dict[str, KnowledgeFileDoc]:
    """``file_id → doc`` of the ready files indexed by the active backend (cached until ``rev`` changes)."""
    entry = _ready_cache.get(agent_id)
    now = time.monotonic()
    if entry is not None and entry.rev == rev and entry.expires > now:
        return entry.files
    active = backend_name()
    files: dict[str, KnowledgeFileDoc] = {}
    async for snap in _files_col(agent_id).where(filter=FieldFilter("status", "==", "ready")).stream():
        doc = KnowledgeFileDoc.model_validate(snap.to_dict())
        if doc.backend == active:
            files[doc.file_id] = doc
    _ready_cache[agent_id] = _ReadyEntry(rev=rev, expires=now + READY_CACHE_TTL_S, files=files)
    return files


async def has_ready_files(agent_id: str) -> bool:
    """True when at least one file is ready in the active backend (decides whether the tool is offered)."""
    active = backend_name()
    async for snap in _files_col(agent_id).where(filter=FieldFilter("status", "==", "ready")).stream():
        if (snap.to_dict() or {}).get("backend", "local") == active:
            return True
    return False


async def search(agent_id: str, query: str, top_k: int | None = None) -> list[Passage]:
    """Ranked passages from the agent's ready files (at most ``top_k``, default ``RAG_TOP_K``)."""
    query = " ".join((query or "").split())[:MAX_QUERY_CHARS]
    if not query:
        return []
    k = max(1, min(top_k or get_settings().rag_top_k, 20))
    snap = await _agent_ref(agent_id).get()
    if not snap.exists:
        return []
    data = snap.to_dict() or {}
    rev = int(data.get("knowledge_rev") or 0)
    store = data.get("knowledge_store")
    backend = get_backend()
    if not store or not backend.owns(store):
        return []
    ready = await _ready_files(agent_id, rev)
    if not ready:
        return []
    passages = await backend.search(agent_id, store, query, k, rev=rev)
    results: list[Passage] = []
    for p in passages:
        doc = ready.get(p.file_id)
        if doc is None:  # unknown, deleted or not-yet-ready file
            continue
        results.append(replace(p, filename=doc.filename))
        if len(results) >= k:
            break
    return results
