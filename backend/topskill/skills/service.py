"""Skills: create (immutable), list, read, and the snapshot helpers used by publish and chat (SPEC §5.1,
§6.4, §14.2).

``skills/{skill_id}`` documents are immutable after creation; files live in the blob store at
``skills/{skill_id}/{relpath}``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from google.cloud import firestore

from topskill.db import Col, get_db, new_id, utcnow
from topskill.domain import SkillFile, SkillRef, iso_z
from topskill.errors import ApiError, Codes
from topskill.skills.parser import (
    SKILL_MD,
    SkillPackage,
    build_package,
    guess_mime,
    is_text_file,
    parse_front_matter,
)
from topskill.storage import get_blob_store, normalize_path

log = logging.getLogger(__name__)

BODY_PREVIEW_CHARS = 4000
MAX_SKILLS_LISTED = 200
MAX_SKILLS_PER_AGENT = 5


def skill_not_found() -> ApiError:
    return ApiError(404, Codes.SKILL_NOT_FOUND, "This skill does not exist.")


def _valid_skill_id(skill_id: str) -> bool:
    return isinstance(skill_id, str) and bool(skill_id) and "/" not in skill_id and len(skill_id) <= 64


def _ref_from_doc(data: dict[str, Any]) -> SkillRef:
    return SkillRef(
        skill_id=data["id"],
        slug=data.get("slug") or "skill",
        name=data.get("name") or "Skill",
        description=data.get("description") or "",
        gcs_prefix=data.get("gcs_prefix") or f"skills/{data['id']}/",
        body_chars=int(data.get("body_chars") or 0),
        files=[SkillFile.model_validate(f) for f in data.get("files") or []],
    )


def skill_to_wire(data: dict[str, Any], *, body_preview: str | None = None) -> dict[str, Any]:
    """``skills/{id}`` document → TS ``Skill`` (``body_preview`` only when given)."""
    created = data.get("created_at")
    out: dict[str, Any] = {
        "id": data["id"],
        "slug": data.get("slug") or "skill",
        "name": data.get("name") or "Skill",
        "description": data.get("description") or "",
        "body_chars": int(data.get("body_chars") or 0),
        "files": [SkillFile.model_validate(f).model_dump() for f in data.get("files") or []],
        "created_at": iso_z(created) if isinstance(created, datetime) else None,
    }
    if body_preview is not None:
        out["body_preview"] = body_preview
    return out


async def create_skill(owner_uid: str, package: SkillPackage) -> dict[str, Any]:
    """Store the package files and create ``skills/{id}``; returns the stored document."""
    skill_id = new_id("skl")
    prefix = f"skills/{skill_id}/"
    store = get_blob_store()
    files: list[SkillFile] = []
    for path, data in package.files.items():
        normalize_path(prefix + path)
        is_text = path == SKILL_MD or is_text_file(path, data)
        mime = "text/markdown" if path == SKILL_MD else guess_mime(path)
        await store.put(prefix + path, data, mime)
        files.append(SkillFile(path=path, size=len(data), mime=mime, is_text=is_text))
    doc = {
        "id": skill_id,
        "owner_uid": owner_uid,
        "slug": package.slug,
        "name": package.name,
        "description": package.description,
        "body_chars": len(package.body),
        "gcs_prefix": prefix,
        "files": [f.model_dump() for f in files],
        "created_at": utcnow(),
    }
    await get_db().collection(Col.SKILLS).document(skill_id).create(doc)
    log.info("skill_created skill_id=%s owner=%s files=%d", skill_id, owner_uid, len(files))
    return doc


async def create_skill_from_entries(
    owner_uid: str, entries: list[tuple[str, bytes]], *, fallback_name: str | None = None
) -> dict[str, Any]:
    return await create_skill(owner_uid, build_package(entries, fallback_name=fallback_name))


async def list_skills(owner_uid: str) -> list[dict[str, Any]]:
    """The owner's skills, newest first. Single-field query on ``owner_uid``; sorted **in memory**."""
    query = get_db().collection(Col.SKILLS).where(filter=firestore.FieldFilter("owner_uid", "==", owner_uid))
    docs = [snap.to_dict() or {} async for snap in query.limit(MAX_SKILLS_LISTED).stream()]
    docs.sort(key=lambda d: (d.get("created_at") or utcnow(), d.get("id", "")), reverse=True)
    return docs


async def get_owned_skill_doc(owner_uid: str, skill_id: str) -> dict[str, Any]:
    if not _valid_skill_id(skill_id):
        raise skill_not_found()
    snap = await get_db().collection(Col.SKILLS).document(skill_id).get()
    data = snap.to_dict() if snap.exists else None
    if not data or data.get("owner_uid") != owner_uid:
        raise skill_not_found()
    return data


async def get_skill_docs(skill_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Batch read of skill docs by id (missing ids are absent from the result)."""
    db = get_db()
    refs = [db.collection(Col.SKILLS).document(s) for s in dict.fromkeys(skill_ids) if _valid_skill_id(s)]
    if not refs:
        return {}
    out: dict[str, dict[str, Any]] = {}
    async for snap in db.get_all(refs):
        if snap.exists:
            out[snap.id] = snap.to_dict() or {}
    return out


async def load_skill_refs(owner_uid: str, skill_ids: list[str]) -> list[SkillRef]:
    """Snapshots of the caller's skills in the given order; 404 ``skill_not_found`` for any id that does
    not exist or is owned by someone else (SPEC §6.1)."""
    if not skill_ids:
        raise ApiError(
            400,
            Codes.VALIDATION_ERROR,
            "Select at least one skill.",
            {"errors": [{"loc": ["body", "skill_ids"], "msg": "at least one skill is required"}]},
        )
    if len(skill_ids) > MAX_SKILLS_PER_AGENT:
        raise ApiError(
            400,
            Codes.VALIDATION_ERROR,
            f"Select at most {MAX_SKILLS_PER_AGENT} skills.",
            {"errors": [{"loc": ["body", "skill_ids"], "msg": "too many skills"}]},
        )
    docs = await get_skill_docs(skill_ids)
    refs: list[SkillRef] = []
    for skill_id in dict.fromkeys(skill_ids):
        data = docs.get(skill_id)
        if not data or data.get("owner_uid") != owner_uid:
            raise skill_not_found()
        refs.append(_ref_from_doc(data))
    return refs


async def read_skill_file(ref: SkillRef, path: str) -> bytes:
    """Raw bytes of one file of an (immutable) skill; 404 ``not_found`` unless ``path`` is in the package."""
    if not isinstance(path, str) or path not in {f.path for f in ref.files}:
        raise ApiError(404, Codes.NOT_FOUND, "File not found.")
    return await get_blob_store().get(ref.gcs_prefix + path)


async def read_skill_body(ref: SkillRef) -> str:
    """The ``SKILL.md`` instructions without front matter."""
    raw = await read_skill_file(ref, SKILL_MD)
    _, body = parse_front_matter(raw.decode("utf-8-sig", errors="replace"))
    return body.strip()


async def skill_with_preview(owner_uid: str, skill_id: str) -> dict[str, Any]:
    data = await get_owned_skill_doc(owner_uid, skill_id)
    ref = _ref_from_doc(data)
    try:
        body = await read_skill_body(ref)
    except ApiError:
        body = ""
    return skill_to_wire(data, body_preview=body[:BODY_PREVIEW_CHARS])
