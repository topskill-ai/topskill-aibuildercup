"""``/api/me``, ``PATCH /api/me``, ``POST /api/me/become-creator`` (SPEC §6.4)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from topskill.auth import CurrentUser, get_current_user
from topskill.db import Col, get_db, utcnow
from topskill.domain import Role, UserDoc, iso_z
from topskill.errors import ApiError, Codes
from topskill.repo import get_user

router = APIRouter(tags=["me"])


class CreatorProfile(BaseModel):
    headline: str = ""
    bio: str = ""


class Me(BaseModel):
    uid: str
    email: str | None
    display_name: str
    photo_url: str | None
    role: Role
    creator_profile: CreatorProfile | None
    created_at: str


class CreatorProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headline: str = Field("", max_length=80)
    bio: str = Field("", max_length=600)

    @field_validator("headline", "bio")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class UpdateMeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(None, min_length=1, max_length=60)
    creator_profile: CreatorProfileIn | None = None

    @field_validator("display_name")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("display_name must not be blank")
        return value


class BecomeCreatorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headline: str | None = Field(None, max_length=80)


def me_from_doc(doc: UserDoc) -> Me:
    profile = None
    if doc.creator_profile is not None:
        profile = CreatorProfile(
            headline=str(doc.creator_profile.get("headline") or ""),
            bio=str(doc.creator_profile.get("bio") or ""),
        )
    return Me(
        uid=doc.uid,
        email=doc.email,
        display_name=doc.display_name,
        photo_url=doc.photo_url,
        role=doc.role,
        creator_profile=profile,
        created_at=iso_z(doc.created_at) or "",
    )


async def _load(request: Request, user: CurrentUser) -> UserDoc:
    doc = getattr(request.state, "user_doc", None)
    if isinstance(doc, UserDoc) and doc.uid == user.uid:
        return doc
    doc = await get_user(user.uid)
    if doc is None:
        raise ApiError(401, Codes.UNAUTHENTICATED, "Please sign in.")
    return doc


@router.get("/me", response_model=Me)
async def get_me(request: Request, user: CurrentUser = Depends(get_current_user)) -> Me:
    return me_from_doc(await _load(request, user))


@router.patch("/me", response_model=Me)
async def update_me(
    body: UpdateMeRequest, request: Request, user: CurrentUser = Depends(get_current_user)
) -> Me:
    doc = await _load(request, user)
    updates: dict[str, Any] = {}
    if body.display_name is not None:
        updates["display_name"] = body.display_name
    if body.creator_profile is not None:
        updates["creator_profile"] = body.creator_profile.model_dump()
    if not updates:
        return me_from_doc(doc)
    updates["updated_at"] = utcnow()
    await get_db().collection(Col.USERS).document(user.uid).update(updates)
    return me_from_doc(doc.model_copy(update=updates))


@router.post("/me/become-creator", response_model=Me)
async def become_creator(
    request: Request,
    body: BecomeCreatorRequest | None = None,
    user: CurrentUser = Depends(get_current_user),
) -> Me:
    doc = await _load(request, user)
    profile = dict(doc.creator_profile or {})
    profile.setdefault("headline", "")
    profile.setdefault("bio", "")
    headline = (body.headline or "").strip() if body else ""
    if headline:
        profile["headline"] = headline
    if doc.role == "creator" and doc.creator_profile == profile:
        return me_from_doc(doc)
    updates: dict[str, Any] = {"role": "creator", "creator_profile": profile, "updated_at": utcnow()}
    await get_db().collection(Col.USERS).document(user.uid).update(updates)
    return me_from_doc(doc.model_copy(update=updates))
