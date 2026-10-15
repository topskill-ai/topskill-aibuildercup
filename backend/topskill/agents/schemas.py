"""Request/response models of the agents API and the derived-field helpers (SPEC §6.3, §6.4, §14.2).

Response models serialise to exactly the TS wire types of §6.3.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from topskill.config import get_settings
from topskill.domain import (
    AccentColor,
    AgentConfig,
    AgentDoc,
    AgentStatus,
    Capability,
    FieldType,
    Pricing,
    PricingMode,
    RecordSchema,
    RecordSchemaIn,
    StarterQuestion,
)

TITLE_MAX = 40
TAGLINE_MAX = 120
DESCRIPTION_MAX = 2000
GREETING_MAX = 600
PLACEHOLDER_MAX = 120
CREATOR_NAME_MAX = 60
CREATOR_HEADLINE_MAX = 80
SOUL_MAX = 6000
MAX_STARTER_QUESTIONS = 3
MAX_CAPABILITIES = 6


# --------------------------------------------------------------------------- derived fields


def avatar_url(avatar_id: str | None) -> str | None:
    """``{PUBLIC_API_BASE}/api/public/avatars/{avatar_id}`` or ``None``."""
    if not avatar_id:
        return None
    return f"{get_settings().public_api_base}/api/public/avatars/{avatar_id}"


def agent_urls(slug: str) -> dict:
    """TS ``AgentUrls``."""
    s = get_settings()
    return {
        "public_page": f"{s.public_web_base}/a/{slug}",
        "a2a_card": f"{s.public_api_base}/a2a/{slug}/.well-known/agent-card.json",
        "a2a_endpoint": f"{s.public_api_base}/a2a/{slug}",
    }


def resolve_starter_questions(agent: AgentDoc) -> list[StarterQuestion]:
    """``None`` → three generic questions built from the capabilities and title; ``[]`` → ``[]``."""
    if agent.starter_questions is not None:
        return list(agent.starter_questions)
    questions: list[StarterQuestion] = []
    for cap in agent.capabilities[:2]:
        name = cap.name.strip()
        if not name:
            continue
        prompt = f"Can you help me with {name[:1].lower() + name[1:]}?"
        if cap.description.strip():
            prompt += f" ({cap.description.strip()})"
        questions.append(StarterQuestion(label=name[:60], prompt=prompt[:300]))
    title = agent.title.strip() or "this agent"
    generic = [
        StarterQuestion(label="What can you help with?", prompt=f"What can {title} help me with?"[:300]),
        StarterQuestion(
            label="How does this work?",
            prompt="How do you work, and what information do you need from me?",
        ),
        StarterQuestion(label="Get started", prompt="I'd like to get started. What should I tell you first?"),
    ]
    for q in generic:
        if len(questions) >= 3:
            break
        questions.append(q)
    return questions[:3]


# --------------------------------------------------------------------------- requests


def _strip(value: str | None) -> str | None:
    return value.strip() if isinstance(value, str) else value


class ConfigIn(BaseModel):
    """``CreateAgentRequest.config`` (skill ids come from the top level)."""

    soul: str = Field("", max_length=SOUL_MAX)
    model: str | None = None
    temperature: float | None = Field(None, ge=0, le=1.5)
    web_search_enabled: bool = True
    record_schema: RecordSchemaIn | None = None


class CreateAgentRequest(BaseModel):
    skill_ids: list[str] = Field(min_length=1, max_length=5)
    slug: str | None = Field(None, max_length=80)
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    tagline: str = Field("", max_length=TAGLINE_MAX)
    description: str = Field("", max_length=DESCRIPTION_MAX)
    avatar_id: str | None = None
    greeting: str = Field("", max_length=GREETING_MAX)
    input_placeholder: str = Field("", max_length=PLACEHOLDER_MAX)
    starter_questions: list[StarterQuestion] | None = Field(None, max_length=MAX_STARTER_QUESTIONS)
    capabilities: list[Capability] = Field(default_factory=list, max_length=MAX_CAPABILITIES)
    accent_color: AccentColor = "blue"
    listed: bool = True
    creator_name: str | None = Field(None, max_length=CREATOR_NAME_MAX)
    creator_headline: str | None = Field(None, max_length=CREATOR_HEADLINE_MAX)
    pricing: Pricing
    config: ConfigIn

    _strip_text = field_validator(
        "title", "tagline", "description", "greeting", "input_placeholder", "slug", mode="before"
    )(_strip)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


class ConfigPatch(BaseModel):
    """``Partial<AgentConfig>``; absent = unchanged."""

    soul: str | None = Field(None, max_length=SOUL_MAX)
    model: str | None = None
    temperature: float | None = Field(None, ge=0, le=1.5)
    web_search_enabled: bool | None = None
    skill_ids: list[str] | None = Field(None, min_length=1, max_length=5)
    record_schema: RecordSchemaIn | None = None


class UpdateAgentRequest(BaseModel):
    """All optional; absent = unchanged. Live fields apply now; ``config`` sets ``config_dirty``."""

    title: str | None = Field(None, min_length=1, max_length=TITLE_MAX)
    tagline: str | None = Field(None, max_length=TAGLINE_MAX)
    description: str | None = Field(None, max_length=DESCRIPTION_MAX)
    avatar_id: str | None = None
    greeting: str | None = Field(None, max_length=GREETING_MAX)
    input_placeholder: str | None = Field(None, max_length=PLACEHOLDER_MAX)
    starter_questions: list[StarterQuestion] | None = Field(None, max_length=MAX_STARTER_QUESTIONS)
    capabilities: list[Capability] | None = Field(None, max_length=MAX_CAPABILITIES)
    accent_color: AccentColor | None = None
    listed: bool | None = None
    creator_name: str | None = Field(None, max_length=CREATOR_NAME_MAX)
    creator_headline: str | None = Field(None, max_length=CREATOR_HEADLINE_MAX)
    pricing: Pricing | None = None
    config: ConfigPatch | None = None

    _strip_text = field_validator(
        "title", "tagline", "description", "greeting", "input_placeholder", mode="before"
    )(_strip)


class DraftRequest(BaseModel):
    skill_ids: list[str] = Field(min_length=1, max_length=5)
    quality: Literal["fast", "best"] = "fast"


class NewVersionRequest(BaseModel):
    note: str = Field("", max_length=200)


class AvatarGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=600)
    accent_color: AccentColor | None = None

    @field_validator("prompt")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value.strip()


# --------------------------------------------------------------------------- responses


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


class AgentStatsOut(BaseModel):
    rounds: int = 0
    paid_rounds: int = 0
    conversations: int = 0
    visitor_gross_cents: int = 0
    creator_earned_cents: int = 0
    a2a_calls: int = 0
    a2a_gross_cents: int = 0
    records: int = 0
    last_activity_at: str | None = None


class AgentUrlsOut(BaseModel):
    public_page: str
    a2a_card: str
    a2a_endpoint: str


class KnowledgeCounts(BaseModel):
    files: int = 0
    ready: int = 0
    processing: int = 0
    failed: int = 0


class AgentOut(BaseModel):
    id: str
    slug: str
    status: AgentStatus
    listed: bool
    title: str
    tagline: str
    description: str
    avatar_id: str | None
    avatar_url: str | None
    greeting: str
    input_placeholder: str
    starter_questions: list[StarterQuestion] | None
    capabilities: list[Capability]
    accent_color: AccentColor
    creator_name: str
    creator_headline: str
    pricing: Pricing
    config: AgentConfig
    config_dirty: bool
    active_version: int
    version_count: int
    skills: list[SkillOut]
    knowledge: KnowledgeCounts
    stats: AgentStatsOut
    urls: AgentUrlsOut
    created_at: str
    updated_at: str


class AgentSummaryOut(BaseModel):
    id: str
    slug: str
    title: str
    tagline: str
    avatar_url: str | None
    accent_color: AccentColor
    status: AgentStatus
    listed: bool
    pricing: Pricing
    active_version: int
    config_dirty: bool
    stats: AgentStatsOut
    updated_at: str


class AgentSummaryList(BaseModel):
    items: list[AgentSummaryOut]


class DraftPricingOut(BaseModel):
    mode: PricingMode
    price_cents: int
    free_rounds: int
    rationale: str


class RecordOptionOut(BaseModel):
    key: str
    label: str


class RecordFieldOut(BaseModel):
    key: str
    label: str
    type: FieldType
    description: str
    options: list[RecordOptionOut] | None
    required: bool
    pii: bool
    show_in_table: bool
    min: float | None
    max: float | None


class AgentDraftOut(BaseModel):
    title: str
    tagline: str
    description: str
    soul: str
    greeting: str
    input_placeholder: str
    starter_questions: list[StarterQuestion]
    capabilities: list[Capability]
    accent_color: AccentColor
    pricing: DraftPricingOut
    record_fields: list[RecordFieldOut]
    web_search_recommended: bool
    avatar_prompt: str
    model_used: str


class VersionSkillOut(BaseModel):
    name: str
    skill_id: str


class VersionSummaryOut(BaseModel):
    n: int
    created_at: str
    note: str
    model: str
    web_search_enabled: bool
    skills: list[VersionSkillOut]
    records_enabled: bool
    active: bool


class VersionList(BaseModel):
    items: list[VersionSummaryOut]


class SlugCheckOut(BaseModel):
    slug: str
    valid: bool
    available: bool
    reason: Literal["slug_invalid", "slug_reserved", "slug_taken"] | None


class AvatarOut(BaseModel):
    avatar_id: str
    avatar_url: str


class OkOut(BaseModel):
    ok: bool = True


class PublicCreatorOut(BaseModel):
    uid: str
    name: str
    headline: str


class PublicPricingOut(BaseModel):
    mode: PricingMode
    price_cents: int
    free_rounds: int


class PublicFeaturesOut(BaseModel):
    knowledge: bool
    web_search: bool
    records: bool
    a2a: bool


class PublicStatsOut(BaseModel):
    conversations: int
    a2a_calls: int


class PublicAgentCardOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    slug: str
    title: str
    tagline: str
    avatar_url: str | None
    accent_color: AccentColor
    creator: PublicCreatorOut
    pricing: PublicPricingOut
    features: PublicFeaturesOut
    stats: PublicStatsOut


class PublicA2AOut(BaseModel):
    enabled: bool
    price_cents: int
    card_url: str
    endpoint: str


class PublicAgentOut(PublicAgentCardOut):
    description: str
    greeting: str
    input_placeholder: str
    starter_questions: list[StarterQuestion]
    capabilities: list[Capability]
    a2a: PublicA2AOut
    status: Literal["active"]


class PublicAgentPage(BaseModel):
    items: list[PublicAgentCardOut]
    next_cursor: str | None


__all__ = [
    "AgentDraftOut",
    "AgentOut",
    "AgentSummaryList",
    "AgentSummaryOut",
    "AvatarGenerateRequest",
    "AvatarOut",
    "ConfigIn",
    "ConfigPatch",
    "CreateAgentRequest",
    "DraftRequest",
    "NewVersionRequest",
    "OkOut",
    "PublicAgentCardOut",
    "PublicAgentOut",
    "PublicAgentPage",
    "RecordSchema",
    "SlugCheckOut",
    "UpdateAgentRequest",
    "VersionList",
    "agent_urls",
    "avatar_url",
    "resolve_starter_questions",
]
