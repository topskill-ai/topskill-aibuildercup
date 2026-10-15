"""``/api/skills`` (SPEC §6.4 Skills). Creator only."""

from __future__ import annotations

from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, Request, UploadFile
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from topskill.auth import CurrentUser, require_creator
from topskill.skills import service
from topskill.skills.parser import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_REQUEST_BYTES,
    MAX_SKILL_MD_BYTES,
    MAX_TOTAL_BYTES,
    read_zip,
    skill_invalid,
    too_large,
)
from topskill.skills.service import skill_to_wire

router = APIRouter(tags=["skills"])

_MAX_ZIP_BYTES = MAX_REQUEST_BYTES


class SkillFileOut(BaseModel):
    path: str
    size: int
    mime: str
    is_text: bool


class SkillOut(BaseModel):
    id: str
    slug: str
    name: str
    description: str
    body_chars: int
    files: list[SkillFileOut]
    created_at: str | None
    body_preview: str | None = None


class SkillList(BaseModel):
    items: list[SkillOut]


def _is_zip(upload: UploadFile) -> bool:
    name = (upload.filename or "").lower()
    return name.endswith(".zip") or (upload.content_type or "") in (
        "application/zip",
        "application/x-zip-compressed",
    )


async def _read_upload(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise too_large(f"'{upload.filename}' is too large.", path=upload.filename)
    return data


@router.post("/skills", status_code=201, response_model=SkillOut, response_model_exclude_unset=True)
async def create_skill(request: Request, user: CurrentUser = Depends(require_creator)) -> dict:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
        raise too_large("The upload is larger than 12 MB.", max_bytes=MAX_REQUEST_BYTES)
    if "multipart/form-data" not in (request.headers.get("content-type") or ""):
        raise skill_invalid("Upload the skill as multipart/form-data.")
    try:
        form = await request.form(max_files=MAX_FILES + 1, max_fields=20)
    except StarletteHTTPException as exc:
        raise skill_invalid(f"The upload could not be read: {exc.detail}") from exc
    try:
        single = [v for v in form.getlist("file") if isinstance(v, UploadFile)]
        many = [v for v in form.getlist("files") if isinstance(v, UploadFile)]
        if single and many:
            raise skill_invalid("Send either one 'file' or several 'files', not both.")
        if not single and not many:
            raise skill_invalid("No files were uploaded. Add a SKILL.md, a folder or a .zip.")
        if len(single) > 1:
            raise skill_invalid("Send one zip archive in 'file'.")
        fallback_name: str | None = None
        if single and _is_zip(single[0]):
            upload = single[0]
            data = await _read_upload(upload, _MAX_ZIP_BYTES)
            entries = read_zip(data)
            fallback_name = PurePosixPath(upload.filename or "skill.zip").stem
        else:
            uploads = single or many
            if len(uploads) > MAX_FILES:
                raise skill_invalid(f"A skill package can have at most {MAX_FILES} files.")
            entries = []
            total = 0
            for upload in uploads:
                name = upload.filename or ""
                if name.lower().endswith(".zip"):
                    raise skill_invalid("Upload a zip archive on its own in 'file'.")
                is_skill_md = PurePosixPath(name).name.lower() == "skill.md"
                limit = MAX_SKILL_MD_BYTES if is_skill_md else MAX_FILE_BYTES
                data = await _read_upload(upload, limit)
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise too_large("The skill package is larger than 10 MB.")
                entries.append((name, data))
    finally:
        await form.close()
    doc = await service.create_skill_from_entries(user.uid, entries, fallback_name=fallback_name)
    return skill_to_wire(doc)


@router.get("/skills", response_model=SkillList, response_model_exclude_unset=True)
async def list_skills(user: CurrentUser = Depends(require_creator)) -> dict:
    docs = await service.list_skills(user.uid)
    return {"items": [skill_to_wire(d) for d in docs]}


@router.get("/skills/{skill_id}", response_model=SkillOut, response_model_exclude_unset=True)
async def get_skill(skill_id: str, user: CurrentUser = Depends(require_creator)) -> dict:
    return await service.skill_with_preview(user.uid, skill_id)
