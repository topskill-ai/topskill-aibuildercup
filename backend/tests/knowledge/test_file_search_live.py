"""Live File Search smoke tests (Gemini Developer API). Never run in CI.

Run manually from ``backend/``:
``uv run pytest -m live tests/knowledge/test_file_search_live.py`` (the second test also needs the
emulators, e.g. via ``deploy/scripts/emu_exec.sh``). The API key is read from Secret Manager.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.core.helpers import settings_env
from tests.knowledge.docs import make_docx, make_pdf
from tests.knowledge.live_helpers import gemini_api_key
from topskill.db import utcnow
from topskill.knowledge import file_search, service
from topskill.knowledge.base import KnowledgeFileDoc, file_type
from topskill.knowledge.extract import extract_text
from topskill.knowledge.file_search import FileSearchBackend

pytestmark = pytest.mark.live


@pytest.fixture
def live_file_search(monkeypatch: pytest.MonkeyPatch):
    key = gemini_api_key()
    with settings_env(monkeypatch, knowledge_backend="file_search", gemini_api_key=key):
        service._backends.clear()
        file_search._client = None
        yield
    service._backends.clear()
    file_search._client = None


def _by_text(passages, needle: str):
    return next((p for p in passages if needle in p.text), None)


async def test_live_file_search_upload_search_pages_delete(live_file_search) -> None:
    backend = FileSearchBackend()
    data = make_pdf()
    text = extract_text(data, "application/pdf")
    now = utcnow()
    file = KnowledgeFileDoc(
        file_id="kf_" + "1" * 20,
        filename="visa_guide.pdf",
        mime="application/pdf",
        size=len(data),
        gcs_path="knowledge/agt_live/kf/visa_guide.pdf",
        backend="file_search",
        created_at=now,
        updated_at=now,
    )
    agent_id = f"agt_live{int(time.time())}"
    store = await backend.create_store(agent_id)
    try:
        assert backend.owns(store)
        started = time.monotonic()
        result = await backend.ingest(agent_id, store, file, data, text)
        assert result.pages == 3 and result.fs_document_name and result.fs_document_name.startswith(store)
        print(f"indexed in {time.monotonic() - started:.1f}s")

        refund = await backend.search(agent_id, store, "refund policy after withdrawal", 4, rev=0)
        assert refund, "no passages for the refund query"
        assert all(p.file_id == file.file_id and p.filename == "visa_guide.pdf" for p in refund)
        hit = _by_text(refund, "70 percent")
        assert hit is not None and hit.page_start is not None and hit.page_end == 3

        priority = await backend.search(agent_id, store, "How much does priority service cost?", 4, rev=0)
        hit = _by_text(priority, "480")
        assert hit is not None and hit.page_start is not None and hit.page_start <= 2 <= (hit.page_end or 0)

        await backend.delete(
            agent_id, store, file.model_copy(update={"fs_document_name": result.fs_document_name})
        )
        client = file_search.get_client()
        docs = [d async for d in await client.aio.file_search_stores.documents.list(parent=store)]
        assert docs == []
    finally:
        await backend.delete_store(store)


@pytest.mark.emulator
async def test_live_service_flow_on_emulator(live_file_search, client, dev_auth) -> None:
    from topskill.repo import get_agent
    from topskill.testing.factories import make_agent, make_user

    owner = await make_user("creator")
    agent = await make_agent(owner)
    headers = dev_auth(owner.uid, "creator")
    resp = await client.post(
        f"/api/agents/{agent.id}/knowledge",
        files={"files": ("visa_guide.pdf", make_pdf(), "application/pdf")},
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    file_id = resp.json()["items"][0]["file_id"]
    store = None
    try:
        for _ in range(90):
            listing = (await client.get(f"/api/agents/{agent.id}/knowledge", headers=headers)).json()
            status = listing["items"][0]["status"]
            if status != "processing":
                break
            await asyncio.sleep(1)
        assert listing["backend"] == "file_search"
        assert status == "ready", listing
        assert listing["items"][0]["pages"] == 3
        stored = await get_agent(agent.id)
        assert stored is not None and stored.knowledge_store and stored.knowledge_rev >= 1
        store = stored.knowledge_store
        assert await service.has_ready_files(agent.id)
        passages = await service.search(agent.id, "What is the refund policy?")
        hit = _by_text(passages, "refund")
        assert hit is not None and hit.file_id == file_id and hit.page_end == 3
        resp = await client.delete(f"/api/agents/{agent.id}/knowledge/{file_id}", headers=headers)
        assert resp.status_code == 200
        assert await service.search(agent.id, "What is the refund policy?") == []
    finally:
        if store:
            await FileSearchBackend().delete_store(store)


NON_PDF_SAMPLES = {
    ".md": b"# Office hours\n\nThe Lumeria office opens at 08:15 and closes at 16:40 on weekdays.\n",
    ".txt": b"The Lumeria office opens at 08:15 and closes at 16:40 on weekdays.\n",
    ".csv": b"service,price_zorkmids,days\nstandard,0,19\npriority,480,5\nsuper priority,1200,1\n",
}


@pytest.mark.parametrize("suffix", [".md", ".txt", ".csv", ".docx"])
async def test_live_upload_each_accepted_type(live_file_search, suffix: str) -> None:
    """Every accepted non-PDF type indexes (csv/docx used to fail with 400 on an explicit mime_type)."""
    filename = f"sample{suffix}"
    data = (
        make_docx(["The Lumeria office opens at 08:15 and closes at 16:40 on weekdays."])
        if suffix == ".docx"
        else NON_PDF_SAMPLES[suffix]
    )
    mime = file_type(filename)
    text = extract_text(data, mime)
    now = utcnow()
    file = KnowledgeFileDoc(
        file_id="kf_" + "2" * 20,
        filename=filename,
        mime=mime,
        size=len(data),
        gcs_path=f"knowledge/agt_live/kf/{filename}",
        backend="file_search",
        created_at=now,
        updated_at=now,
    )
    backend = FileSearchBackend()
    store = await backend.create_store(f"agt_live{suffix[1:]}{int(time.time())}")
    try:
        result = await backend.ingest("agt_live", store, file, data, text)
        assert result.fs_document_name and result.fs_document_name.startswith(store)
        assert result.pages is None
        await backend.delete(
            "agt_live", store, file.model_copy(update={"fs_document_name": result.fs_document_name})
        )
        client = file_search.get_client()
        assert [d async for d in await client.aio.file_search_stores.documents.list(parent=store)] == []
    finally:
        await backend.delete_store(store)
