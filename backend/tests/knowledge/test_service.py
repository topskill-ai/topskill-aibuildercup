"""Knowledge service and endpoints on the Firestore emulator with the ``local`` backend."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from tests.knowledge.docs import make_pdf
from topskill.db import Col, get_db, new_id, utcnow
from topskill.domain import Passage
from topskill.errors import ApiError
from topskill.knowledge import service
from topskill.knowledge.base import (
    DELETE_ERROR,
    STALE_ERROR,
    UNPARSEABLE_ERROR,
    UNREADABLE_ERROR,
    ExtractedText,
    IndexingError,
    IngestResult,
    KnowledgeFileDoc,
)
from topskill.knowledge.tool import make_search_tool
from topskill.repo import get_agent
from topskill.storage import get_blob_store
from topskill.testing.factories import make_agent, make_user

pytestmark = pytest.mark.emulator

PDF = "application/pdf"


async def _wait(agent_id: str, file_id: str) -> KnowledgeFileDoc:
    deadline = asyncio.get_running_loop().time() + 20.0
    while True:
        doc = await service.get_file(agent_id, file_id)
        assert doc is not None
        if doc.status != "processing":
            return doc
        assert asyncio.get_running_loop().time() < deadline, f"{file_id} still processing"
        await asyncio.sleep(0.1)


async def _rev(agent_id: str) -> int:
    agent = await get_agent(agent_id)
    assert agent is not None
    return agent.knowledge_rev


async def _owner_agent():
    owner = await make_user("creator")
    return owner, await make_agent(owner)


async def _upload(client, dev_auth, owner, agent, name: str, data: bytes, mime: str = PDF):
    return await client.post(
        f"/api/agents/{agent.id}/knowledge",
        files={"files": (name, data, mime)},
        headers=dev_auth(owner.uid, "creator"),
    )


async def test_upload_ingest_list_search_delete(client, dev_auth) -> None:
    owner, agent = await _owner_agent()
    headers = dev_auth(owner.uid, "creator")
    resp = await _upload(client, dev_auth, owner, agent, "visa_guide.pdf", make_pdf())
    assert resp.status_code == 202, resp.text
    [item] = resp.json()["items"]
    assert item["status"] == "processing" and item["filename"] == "visa_guide.pdf" and item["mime"] == PDF
    assert item["created_at"].endswith("Z") and item["pages"] is None
    file_id = item["file_id"]

    doc = await _wait(agent.id, file_id)
    assert doc.status == "ready" and doc.pages == 3 and doc.backend == "local" and doc.chunks == 3
    stored = await get_agent(agent.id)
    # the local backend needs no store: knowledge_store stays unset (write-once, File Search only)
    assert stored is not None and stored.knowledge_store is None and stored.knowledge_rev == 1

    listing = (await client.get(f"/api/agents/{agent.id}/knowledge", headers=headers)).json()
    assert listing["backend"] == "local"
    assert [(f["file_id"], f["status"], f["pages"], f["error"]) for f in listing["items"]] == [
        (file_id, "ready", 3, None)
    ]
    assert set(listing["items"][0]) == {
        "file_id",
        "filename",
        "mime",
        "size",
        "status",
        "error",
        "pages",
        "created_at",
        "updated_at",
    }

    assert await service.has_ready_files(agent.id)
    passages = await service.search(agent.id, "What is the refund policy?")
    assert passages and passages[0].page_start == 3 and passages[0].filename == "visa_guide.pdf"
    assert passages[0].file_id == file_id and passages[0].score and passages[0].score > 0
    priority = await service.search(agent.id, "priority service cost", top_k=1)
    assert len(priority) == 1 and priority[0].page_start == 2

    out = await make_search_tool(agent.id, None).func(query="refund")
    assert out["results"][0] == {
        "ref": "[1]",
        "source": "visa_guide.pdf",
        "pages": "3",
        "text": out["results"][0]["text"],
    }

    console = await client.post(
        f"/api/agents/{agent.id}/knowledge/search", json={"query": "refund"}, headers=headers
    )
    assert console.status_code == 200 and console.json()["items"][0]["n"] == 1
    assert console.json()["items"][0]["page_start"] == 3

    resp = await client.delete(f"/api/agents/{agent.id}/knowledge/{file_id}", headers=headers)
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    assert await _rev(agent.id) == 2
    assert await service.get_file(agent.id, file_id) is None
    assert not await service.has_ready_files(agent.id)
    assert await service.search(agent.id, "refund") == []
    chunks = get_db().collection(Col.AGENTS).document(agent.id).collection(Col.KNOWLEDGE_CHUNKS)
    assert [s async for s in chunks.stream()] == []
    with pytest.raises(ApiError):
        await get_blob_store().get(doc.gcs_path)


async def test_text_files_have_no_page_numbers(client, dev_auth) -> None:
    owner, agent = await _owner_agent()
    body = ("# FAQ\n\nThe office opens at nine.\n" + "Filler sentence about nothing. " * 80).encode()
    resp = await _upload(client, dev_auth, owner, agent, "faq.md", body, "text/markdown")
    doc = await _wait(agent.id, resp.json()["items"][0]["file_id"])
    assert doc.status == "ready" and doc.pages is None and doc.mime == "text/markdown" and doc.chunks >= 2
    [hit] = await service.search(agent.id, "When does the office open?", top_k=1)
    assert "nine" in hit.text and hit.page_start is None and hit.page_end is None


async def test_unreadable_and_corrupt_files(client, dev_auth) -> None:
    owner, agent = await _owner_agent()
    blank = await _upload(client, dev_auth, owner, agent, "scan.pdf", make_pdf([], blank_pages=2))
    corrupt = await _upload(client, dev_auth, owner, agent, "broken.pdf", b"%PDF-1.4 garbage")
    blank_doc = await _wait(agent.id, blank.json()["items"][0]["file_id"])
    corrupt_doc = await _wait(agent.id, corrupt.json()["items"][0]["file_id"])
    assert (blank_doc.status, blank_doc.error, blank_doc.pages) == ("unreadable", UNREADABLE_ERROR, 2)
    assert (corrupt_doc.status, corrupt_doc.error) == ("unreadable", UNPARSEABLE_ERROR)
    assert await _rev(agent.id) == 2
    assert not await service.has_ready_files(agent.id)
    stored = await get_agent(agent.id)
    assert stored is not None and stored.knowledge_store is None  # never indexed → no store needed
    # unreadable files are kept (the creator can see and delete them)
    assert blank_doc.gcs_path and await get_blob_store().get(blank_doc.gcs_path)


async def test_failed_indexing_bumps_rev_and_cleans_up(client, dev_auth, monkeypatch) -> None:
    owner, agent = await _owner_agent()
    local = service.backend_for("local")
    deleted: list[str] = []

    async def failing_ingest(*args, **kwargs):
        raise IndexingError(
            "boom", user_message="Indexing failed. Please try again later or re-upload the file."
        )

    async def recording_delete(agent_id, store, file):
        deleted.append(file.file_id)

    monkeypatch.setattr(local, "ingest", failing_ingest)
    monkeypatch.setattr(local, "delete", recording_delete)
    resp = await _upload(client, dev_auth, owner, agent, "guide.pdf", make_pdf())
    file_id = resp.json()["items"][0]["file_id"]
    doc = await _wait(agent.id, file_id)
    assert doc.status == "failed" and doc.error and "Indexing failed" in doc.error
    assert deleted == [file_id] and await _rev(agent.id) == 1


async def test_ingest_crash_marks_failed(monkeypatch) -> None:
    _owner, agent = await _owner_agent()
    [doc] = await service.add_files(agent, [service.Upload("a.txt", b"hello world")])

    async def crash(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(service, "ensure_store", crash)
    await service.ingest_file(agent.id, doc.file_id)  # never raises
    failed = await _wait(agent.id, doc.file_id)
    assert failed.status == "failed" and failed.error
    await service.ingest_file(agent.id, "kf_" + "0" * 20)  # unknown file: no-op


async def test_delete_while_processing_drops_indexed_data(monkeypatch) -> None:
    _owner, agent = await _owner_agent()
    local = service.backend_for("local")
    gate = asyncio.Event()
    entered = asyncio.Event()
    cleaned: list[str | None] = []
    real_delete = local.delete

    async def slow_ingest(agent_id, store, file, data, text):
        entered.set()
        await gate.wait()
        return IngestResult(pages=None, chunks=0)

    async def tracking_delete(agent_id, store, file):
        cleaned.append(file.file_id)
        await real_delete(agent_id, store, file)

    monkeypatch.setattr(local, "ingest", slow_ingest)
    monkeypatch.setattr(local, "delete", tracking_delete)
    [doc] = await service.add_files(agent, [service.Upload("a.txt", b"hello world")])
    await asyncio.wait_for(entered.wait(), 5)  # the task reached the backend
    await service.delete_file(agent.id, doc.file_id)
    gate.set()
    for _ in range(50):
        await asyncio.sleep(0.05)
        if len(cleaned) >= 2:
            break
    assert cleaned == [doc.file_id, doc.file_id]  # once by delete, once by the ingestion task
    assert await service.get_file(agent.id, doc.file_id) is None


async def test_stale_processing_sweep(client, dev_auth) -> None:
    owner, agent = await _owner_agent()
    col = get_db().collection(Col.AGENTS).document(agent.id).collection(Col.KNOWLEDGE)
    old, fresh = new_id("kf"), new_id("kf")
    for file_id, age in ((old, timedelta(minutes=11)), (fresh, timedelta(minutes=1))):
        ts = utcnow() - age
        await col.document(file_id).set(
            KnowledgeFileDoc(
                file_id=file_id,
                filename=f"{file_id}.pdf",
                mime=PDF,
                size=1,
                gcs_path=f"knowledge/{agent.id}/{file_id}/x.pdf",
                created_at=ts,
                updated_at=ts,
            ).model_dump(mode="python")
        )
    listing = (
        await client.get(f"/api/agents/{agent.id}/knowledge", headers=dev_auth(owner.uid, "creator"))
    ).json()
    status = {f["file_id"]: (f["status"], f["error"]) for f in listing["items"]}
    assert status == {old: ("failed", STALE_ERROR), fresh: ("processing", None)}
    assert await _rev(agent.id) == 1
    assert await service.sweep_stale(agent.id) == 0

    other = await make_agent(owner)
    ts = utcnow() - timedelta(minutes=30)
    stale_id = new_id("kf")
    await (
        get_db()
        .collection(Col.AGENTS)
        .document(other.id)
        .collection(Col.KNOWLEDGE)
        .document(stale_id)
        .set(
            KnowledgeFileDoc(
                file_id=stale_id,
                filename="s.pdf",
                mime=PDF,
                size=1,
                gcs_path="x",
                created_at=ts,
                updated_at=ts,
            ).model_dump(mode="python")
        )
    )
    assert await service.sweep_stale_all() >= 1
    swept = await service.get_file(other.id, stale_id)
    assert swept is not None and swept.status == "failed" and swept.error == STALE_ERROR


async def test_upload_validation_and_limits(client, dev_auth) -> None:
    owner, agent = await _owner_agent()
    headers = dev_auth(owner.uid, "creator")
    url = f"/api/agents/{agent.id}/knowledge"

    resp = await client.post(
        url, files={"files": ("run.exe", b"MZ", "application/octet-stream")}, headers=headers
    )
    assert resp.status_code == 400 and resp.json()["code"] == "unsupported_file_type"
    resp = await client.post(url, files={"files": ("empty.txt", b"", "text/plain")}, headers=headers)
    assert resp.status_code == 400 and resp.json()["code"] == "validation_error"
    resp = await client.post(
        url, files={"files": ("big.txt", b"a" * (10 * 1024 * 1024 + 1), "text/plain")}, headers=headers
    )
    assert resp.status_code == 413 and resp.json()["code"] == "payload_too_large"
    four = [("files", (f"f{i}.txt", b"x", "text/plain")) for i in range(4)]
    resp = await client.post(url, files=four, headers=headers)
    assert resp.status_code == 400 and resp.json()["code"] == "validation_error"
    assert (await client.get(url, headers=headers)).json()["items"] == []

    col = get_db().collection(Col.AGENTS).document(agent.id).collection(Col.KNOWLEDGE)
    now = utcnow()
    for i in range(20):
        file_id = new_id("kf")
        await col.document(file_id).set(
            KnowledgeFileDoc(
                file_id=file_id,
                filename=f"f{i}.txt",
                mime="text/plain",
                size=1,
                gcs_path="x",
                status="ready",
                created_at=now,
                updated_at=now,
            ).model_dump(mode="python")
        )
    resp = await client.post(url, files={"files": ("one-more.txt", b"x", "text/plain")}, headers=headers)
    assert resp.status_code == 400 and resp.json()["code"] == "knowledge_limit_reached"
    assert resp.json()["detail"] == {"max_files": 20, "current": 20}


async def test_owner_scoping(client, dev_auth) -> None:
    owner, agent = await _owner_agent()
    other = await make_user("creator")
    visitor = await make_user("visitor")
    url = f"/api/agents/{agent.id}/knowledge"
    resp = await client.get(url, headers=dev_auth(other.uid, "creator"))
    assert resp.status_code == 404 and resp.json()["code"] == "agent_not_found"
    resp = await client.post(url, files={"files": ("a.txt", b"x", "text/plain")}, headers=dev_auth(other.uid))
    assert resp.status_code == 404
    resp = await client.get(url, headers=dev_auth(visitor.uid, "visitor"))
    assert resp.status_code == 403 and resp.json()["code"] == "creator_required"
    resp = await client.get(url)
    assert resp.status_code == 401
    headers = dev_auth(owner.uid, "creator")
    for bad in ("kf_" + "0" * 20, "not-an-id", "..%2F..%2Fx"):
        resp = await client.delete(f"{url}/{bad}", headers=headers)
        assert resp.status_code == 404, bad
    [doc] = await service.add_files(agent, [service.Upload("a.txt", b"hello")])
    resp = await client.delete(
        f"/api/agents/{(await make_agent(other)).id}/knowledge/{doc.file_id}", headers=dev_auth(other.uid)
    )
    assert resp.status_code == 404  # another agent's file id is not found under the caller's agent


async def test_search_drops_passages_of_files_that_are_not_ready() -> None:
    _owner, agent = await _owner_agent()
    [doc] = await service.add_files(agent, [service.Upload("a.txt", b"zebra crossing rules")])
    ready = await _wait(agent.id, doc.file_id)
    assert ready.status == "ready"
    # a chunk whose file is unknown / processing never surfaces
    chunks = get_db().collection(Col.AGENTS).document(agent.id).collection(Col.KNOWLEDGE_CHUNKS)
    await chunks.document("kf_ghost_0000").set(
        {
            "file_id": "kf_ghost",
            "filename": "ghost.txt",
            "i": 0,
            "text": "zebra zebra zebra",
            "page_start": None,
            "page_end": None,
            "terms": {"zebra": 3},
        }
    )
    service.backend_for("local")._cache.clear()  # type: ignore[attr-defined]
    passages = await service.search(agent.id, "zebra")
    assert [p.file_id for p in passages] == [doc.file_id]
    # the ghost chunk scores higher; it must not take the only slot (the backend is asked for more)
    assert [p.file_id for p in await service.search(agent.id, "zebra", top_k=1)] == [doc.file_id]
    assert await service.search(agent.id, "   ") == []
    assert await service.search("agt_" + "0" * 20, "zebra") == []


class CountingBackend:
    """A backend whose store creation is slow, to provoke races."""

    name = "local"
    needs_store = True

    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []

    def owns(self, store: str | None) -> bool:
        return bool(store) and store.startswith("fake/")

    async def create_store(self, agent_id: str) -> str:
        name = f"fake/{agent_id}/{len(self.created)}"
        self.created.append(name)
        await asyncio.sleep(0.05)
        return name

    async def delete_store(self, store: str) -> None:
        self.deleted.append(store)

    async def ingest(self, agent_id, store, file, data, text: ExtractedText) -> IngestResult:
        return IngestResult(pages=None, chunks=0)

    async def delete(self, agent_id, store, file) -> None:
        return None

    async def search(self, agent_id, store, query, top_k, *, rev) -> list[Passage]:
        return []


async def test_concurrent_ensure_store_creates_exactly_one_store(monkeypatch) -> None:
    _owner, agent = await _owner_agent()
    backend = CountingBackend()
    monkeypatch.setattr(service, "get_backend", lambda: backend)
    names = await asyncio.gather(*(service.ensure_store(agent.id) for _ in range(10)))
    assert len(backend.created) == 1 and set(names) == {backend.created[0]} and backend.deleted == []
    stored = await get_agent(agent.id)
    assert stored is not None and stored.knowledge_store == backend.created[0]
    assert await service.ensure_store(agent.id) == backend.created[0] and len(backend.created) == 1


async def test_store_claim_without_lock_keeps_one_winner(monkeypatch) -> None:
    """Two processes (no shared lock) race: T9 keeps the first claim, the loser's store is deleted."""
    _owner, agent = await _owner_agent()
    backend = CountingBackend()
    monkeypatch.setattr(service, "get_backend", lambda: backend)
    monkeypatch.setattr(service, "_lock", lambda registry, key: asyncio.Lock())
    names = await asyncio.gather(*(service.ensure_store(agent.id) for _ in range(4)))
    stored = await get_agent(agent.id)
    assert stored is not None and stored.knowledge_store is not None
    assert set(names) == {stored.knowledge_store}
    assert len(backend.created) == 4
    assert sorted(backend.deleted) == sorted(n for n in backend.created if n != stored.knowledge_store)


async def test_upload_starts_one_task_per_file(client, dev_auth) -> None:
    owner, agent = await _owner_agent()
    files = [("files", (f"note{i}.txt", f"note number {i}".encode(), "text/plain")) for i in range(3)]
    resp = await client.post(
        f"/api/agents/{agent.id}/knowledge", files=files, headers=dev_auth(owner.uid, "creator")
    )
    assert resp.status_code == 202 and len(resp.json()["items"]) == 3
    docs = [await _wait(agent.id, item["file_id"]) for item in resp.json()["items"]]
    assert [d.status for d in docs] == ["ready"] * 3
    assert await _rev(agent.id) == 3
    counts = await service.counts(agent.id)
    assert counts == {"files": 3, "ready": 3, "processing": 0, "failed": 0}


async def test_store_is_write_once_across_backend_fallback(client, dev_auth) -> None:
    """A File Search store survives a fallback to local and back: local never writes knowledge_store."""
    owner, agent = await _owner_agent()
    fs_store = "fileSearchStores/topskill-keep-me"
    await get_db().collection(Col.AGENTS).document(agent.id).update({"knowledge_store": fs_store})
    resp = await _upload(client, dev_auth, owner, agent, "visa_guide.pdf", make_pdf())
    doc = await _wait(agent.id, resp.json()["items"][0]["file_id"])
    assert doc.status == "ready" and doc.backend == "local"
    stored = await get_agent(agent.id)
    assert stored is not None and stored.knowledge_store == fs_store
    assert await service.ensure_store(agent.id) == "local"
    hits = await service.search(agent.id, "refund policy")
    assert hits and hits[0].file_id == doc.file_id  # local search does not need the stored name


async def test_file_search_claims_over_legacy_local_value(monkeypatch) -> None:
    _owner, agent = await _owner_agent()
    await get_db().collection(Col.AGENTS).document(agent.id).update({"knowledge_store": "local"})
    backend = CountingBackend()
    monkeypatch.setattr(service, "get_backend", lambda: backend)
    name = await service.ensure_store(agent.id)
    assert name == backend.created[0]
    stored = await get_agent(agent.id)
    assert stored is not None and stored.knowledge_store == name
    # once claimed it is never replaced
    assert await service._claim_store(agent.id, "fake/other") == name


async def test_delete_failure_keeps_the_doc_and_answers_503(client, dev_auth, monkeypatch) -> None:
    owner, agent = await _owner_agent()
    headers = dev_auth(owner.uid, "creator")
    [doc] = await service.add_files(agent, [service.Upload("a.txt", b"zebra crossing rules")])
    assert (await _wait(agent.id, doc.file_id)).status == "ready"
    local = service.backend_for("local")
    real_delete = local.delete
    calls: list[str] = []

    async def flaky_delete(agent_id, store, file):
        calls.append(file.file_id)
        if len(calls) == 1:
            raise RuntimeError("503 from the index")
        await real_delete(agent_id, store, file)

    monkeypatch.setattr(local, "delete", flaky_delete)
    rev = await _rev(agent.id)
    resp = await client.delete(f"/api/agents/{agent.id}/knowledge/{doc.file_id}", headers=headers)
    assert resp.status_code == 503 and resp.json()["code"] == "service_unavailable"
    kept = await service.get_file(agent.id, doc.file_id)
    assert kept is not None and (kept.status, kept.error) == ("failed", DELETE_ERROR)
    assert await _rev(agent.id) == rev + 1
    assert await service.search(agent.id, "zebra") == []  # no longer ready → out of search
    resp = await client.delete(f"/api/agents/{agent.id}/knowledge/{doc.file_id}", headers=headers)
    assert resp.status_code == 200
    assert await service.get_file(agent.id, doc.file_id) is None and calls == [doc.file_id] * 2
    chunks = get_db().collection(Col.AGENTS).document(agent.id).collection(Col.KNOWLEDGE_CHUNKS)
    assert [s async for s in chunks.stream()] == []


async def test_delete_redoes_cleanup_when_ingestion_finishes_meanwhile(monkeypatch) -> None:
    """Ingestion records its document between the cleanup and the doc delete: cleanup runs again."""
    _owner, agent = await _owner_agent()
    [doc] = await service.add_files(agent, [service.Upload("a.txt", b"zebra crossing rules")])
    await _wait(agent.id, doc.file_id)
    local = service.backend_for("local")
    seen: list[str | None] = []

    async def racing_delete(agent_id, store, file):
        seen.append(file.fs_document_name)
        if len(seen) == 1:  # ingestion commits right after our read of the doc
            await service._set_status(agent_id, file.file_id, {"fs_document_name": "late/doc"})

    monkeypatch.setattr(local, "delete", racing_delete)
    await service.delete_file(agent.id, doc.file_id)
    assert seen == [None, "late/doc"]
    assert await service.get_file(agent.id, doc.file_id) is None


async def test_counts_sweeps_stale_processing_files() -> None:
    _owner, agent = await _owner_agent()
    ts = utcnow() - timedelta(minutes=11)
    file_id = new_id("kf")
    await (
        get_db()
        .collection(Col.AGENTS)
        .document(agent.id)
        .collection(Col.KNOWLEDGE)
        .document(file_id)
        .set(
            KnowledgeFileDoc(
                file_id=file_id,
                filename="s.pdf",
                mime=PDF,
                size=1,
                gcs_path="x",
                created_at=ts,
                updated_at=ts,
            ).model_dump(mode="python")
        )
    )
    assert await service.counts(agent.id) == {"files": 1, "ready": 0, "processing": 0, "failed": 1}
