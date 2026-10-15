"""Creator agent routes (SPEC §6.4 Agents — creator), prefix ``/api``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from topskill.agents import avatar as avatars
from topskill.agents import draft as drafts
from topskill.agents import service
from topskill.agents.schemas import (
    AgentDraftOut,
    AgentOut,
    AgentSummaryList,
    AvatarGenerateRequest,
    AvatarOut,
    CreateAgentRequest,
    DraftRequest,
    NewVersionRequest,
    OkOut,
    SlugCheckOut,
    UpdateAgentRequest,
    VersionList,
)
from topskill.agents.slugs import check_slug
from topskill.auth import CurrentUser, require_creator
from topskill.errors import ApiError, Codes

router = APIRouter(tags=["agents"])

_AVATAR_FORM_LIMIT = avatars.MAX_UPLOAD_BYTES + 64 * 1024


# Static paths are declared before /agents/{agent_id}.
@router.post("/agents/draft", response_model=AgentDraftOut)
async def draft_agent(body: DraftRequest, user: CurrentUser = Depends(require_creator)) -> dict:
    return await drafts.draft_agent(user.uid, list(dict.fromkeys(body.skill_ids)), body.quality)


@router.get("/agents/slug-check", response_model=SlugCheckOut)
async def slug_check(
    slug: str = Query("", max_length=200), user: CurrentUser = Depends(require_creator)
) -> dict:
    return await check_slug(slug)


@router.post("/avatars", response_model=AvatarOut)
async def upload_avatar(request: Request, user: CurrentUser = Depends(require_creator)) -> dict:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _AVATAR_FORM_LIMIT:
        raise _avatar_too_large()
    try:
        form = await request.form(max_files=1, max_fields=5)
    except StarletteHTTPException as exc:
        raise ApiError(400, Codes.VALIDATION_ERROR, "Upload one image in 'file'.") from exc
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ApiError(
                400,
                Codes.VALIDATION_ERROR,
                "Upload one image in 'file'.",
                {"errors": [{"loc": ["body", "file"], "msg": "field required"}]},
            )
        data = await upload.read(avatars.MAX_UPLOAD_BYTES + 1)
        if len(data) > avatars.MAX_UPLOAD_BYTES:
            raise _avatar_too_large()
    finally:
        await form.close()
    return await avatars.store_avatar(data)


def _avatar_too_large() -> ApiError:
    return ApiError(
        413,
        Codes.PAYLOAD_TOO_LARGE,
        "The image is larger than 5 MB.",
        {"max_bytes": avatars.MAX_UPLOAD_BYTES},
    )


@router.post("/avatars/generate", response_model=AvatarOut)
async def generate_avatar(body: AvatarGenerateRequest, user: CurrentUser = Depends(require_creator)) -> dict:
    return await avatars.generate_avatar(user.uid, body.prompt, body.accent_color)


@router.post("/agents", status_code=201, response_model=AgentOut)
async def create_agent(body: CreateAgentRequest, user: CurrentUser = Depends(require_creator)) -> dict:
    agent = await service.create_agent(user, body)
    return await service.agent_to_wire(agent)


@router.get("/agents", response_model=AgentSummaryList)
async def list_agents(user: CurrentUser = Depends(require_creator)) -> dict:
    return {"items": await service.list_summaries(user.uid)}


@router.get("/agents/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, user: CurrentUser = Depends(require_creator)) -> dict:
    agent = await service.owned_agent(agent_id, user)
    return await service.agent_to_wire(agent)


@router.patch("/agents/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: str, body: UpdateAgentRequest, user: CurrentUser = Depends(require_creator)
) -> dict:
    agent = await service.owned_agent(agent_id, user)
    updated = await service.update_agent(agent, user, body)
    return await service.agent_to_wire(updated)


@router.get("/agents/{agent_id}/versions", response_model=VersionList)
async def list_versions(agent_id: str, user: CurrentUser = Depends(require_creator)) -> dict:
    agent = await service.owned_agent(agent_id, user)
    return {"items": await service.list_versions(agent)}


@router.post("/agents/{agent_id}/versions", response_model=AgentOut)
async def create_version(
    agent_id: str, body: NewVersionRequest | None = None, user: CurrentUser = Depends(require_creator)
) -> dict:
    agent = await service.owned_agent(agent_id, user)
    updated = await service.create_version(agent, user, (body.note if body else "") or "")
    return await service.agent_to_wire(updated)


@router.post("/agents/{agent_id}/versions/{n}/activate", response_model=AgentOut)
async def activate_version(agent_id: str, n: int, user: CurrentUser = Depends(require_creator)) -> dict:
    agent = await service.owned_agent(agent_id, user)
    updated = await service.activate_version(agent, n)
    return await service.agent_to_wire(updated)


@router.post("/agents/{agent_id}/pause", response_model=AgentOut)
async def pause_agent(agent_id: str, user: CurrentUser = Depends(require_creator)) -> dict:
    agent = await service.owned_agent(agent_id, user)
    return await service.agent_to_wire(await service.set_status(agent, "paused"))


@router.post("/agents/{agent_id}/resume", response_model=AgentOut)
async def resume_agent(agent_id: str, user: CurrentUser = Depends(require_creator)) -> dict:
    agent = await service.owned_agent(agent_id, user)
    return await service.agent_to_wire(await service.set_status(agent, "active"))


@router.delete("/agents/{agent_id}", response_model=OkOut)
async def delete_agent(agent_id: str, user: CurrentUser = Depends(require_creator)) -> dict:
    agent = await service.owned_agent(agent_id, user)
    await service.soft_delete(agent)
    return {"ok": True}
