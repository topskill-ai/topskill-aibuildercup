"""``GET /api/public/meta`` (SPEC §6.4): settings the web app must never hardcode."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from topskill.config import get_settings

router = APIRouter(tags=["meta"])


class MandateDefaults(BaseModel):
    max_amount_cents: int
    budget_cents: int
    ttl_minutes: int
    max_budget_cents: int
    max_ttl_minutes: int


class Meta(BaseModel):
    chat_models: list[str]
    default_chat_model: str
    revenue_share_bp: int
    signup_grant_cents: int
    demo_topup_cents: int
    demo_topup_daily_limit: int
    a2a_mandate_defaults: MandateDefaults


def build_meta() -> Meta:
    s = get_settings()
    return Meta(
        chat_models=list(s.chat_model_allowlist),
        default_chat_model=s.model_chat,
        revenue_share_bp=s.creator_revenue_share_bp,
        signup_grant_cents=s.signup_grant_cents,
        demo_topup_cents=s.demo_topup_cents,
        demo_topup_daily_limit=s.demo_topup_daily_limit,
        a2a_mandate_defaults=MandateDefaults(
            max_amount_cents=s.mandate_default_max_amount_cents,
            budget_cents=s.mandate_default_budget_cents,
            ttl_minutes=s.mandate_default_ttl_minutes,
            max_budget_cents=s.mandate_max_budget_cents,
            max_ttl_minutes=s.mandate_max_ttl_minutes,
        ),
    )


@router.get("/meta", response_model=Meta)
async def get_meta() -> Meta:
    return build_meta()
