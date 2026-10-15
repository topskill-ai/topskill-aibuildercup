"""Knowledge endpoints (SPEC §6.4): upload, list (+ stale sweep), delete, owner test search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from topskill.auth import CurrentUser, require_creator
from topskill.errors import ApiError, Codes
from topskill.knowledge import service
from topskill.knowledge.base import MAX_FILE_BYTES, MAX_FILES_PER_REQUEST, clean_filename, file_type
from topskill.repo import get_owned_agent

router = APIRouter(tags=["knowledge"])


@router.post("/agents/{agent_id}/knowledge", status_code=202)
async def upload_knowledge(
    agent_id: str,
    files: list[UploadFile] = File(...),
    user: CurrentUser = Depends(require_creator),
) -> JSONResponse:
    """Upload 1..3 files (the web client sends one per request); ingestion runs in the background."""
    agent = await get_owned_agent(agent_id, user)
    if not 1 <= len(files) <= MAX_FILES_PER_REQUEST:
        raise ApiError(
            400,
            Codes.VALIDATION_ERROR,
            f"Upload between 1 and {MAX_FILES_PER_REQUEST} files per request.",
            {"max_files_per_request": MAX_FILES_PER_REQUEST},
        )
    uploads: list[service.Upload] = []
    for upload in files:
        name = clean_filename(upload.filename)
        file_type(name)  # reject unsupported types before reading the body
        data = await upload.read(MAX_FILE_BYTES + 1)
        uploads.append(service.Upload(filename=name, data=data))
    created = await service.add_files(agent, uploads)
    return JSONResponse(status_code=202, content={"items": [f.to_wire() for f in created]})


@router.get("/agents/{agent_id}/knowledge")
async def list_knowledge(agent_id: str, user: CurrentUser = Depends(require_creator)) -> dict:
    await get_owned_agent(agent_id, user)
    files = await service.list_files(agent_id)
    return {"items": [f.to_wire() for f in files], "backend": service.backend_name()}


@router.delete("/agents/{agent_id}/knowledge/{file_id}")
async def delete_knowledge(agent_id: str, file_id: str, user: CurrentUser = Depends(require_creator)) -> dict:
    await get_owned_agent(agent_id, user)
    await service.delete_file(agent_id, file_id)
    return {"ok": True}


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)


@router.post("/agents/{agent_id}/knowledge/search")
async def search_knowledge_console(
    agent_id: str, body: SearchRequest, user: CurrentUser = Depends(require_creator)
) -> dict:
    """Owner test console [SHOULD]: passages numbered 1..k."""
    await get_owned_agent(agent_id, user)
    if not body.query.strip():
        raise ApiError(400, Codes.VALIDATION_ERROR, "Enter a search query.")
    try:
        passages = await service.search(agent_id, body.query)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(502, Codes.UPSTREAM_ERROR, "The document search failed. Please try again.") from exc
    return {
        "items": [
            {
                "n": n,
                "file_id": p.file_id,
                "filename": p.filename,
                "text": p.text,
                "page_start": p.page_start,
                "page_end": p.page_end,
                "score": p.score,
            }
            for n, p in enumerate(passages, start=1)
        ]
    }
