"""Shared document models and every cross-package value type (SPEC §5.2).

Domain packages implement and re-export these types; they never define their own copies. Firestore stores
the pydantic models via ``model.model_dump(mode="python")`` (datetimes native); readers use
``Model.model_validate(snapshot.to_dict())``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol

from google.cloud.firestore_v1.async_transaction import AsyncTransaction
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from google.genai import types

Role = Literal["visitor", "creator"]
AccentColor = Literal["amber", "blue", "emerald", "rose", "violet"]
PricingMode = Literal["free", "per_round"]
AgentStatus = Literal["active", "paused"]
FieldType = Literal["single_choice", "multi_choice", "number", "boolean", "text", "long_text"]
CapabilityIcon = Literal[
    "sparkles",
    "book-open",
    "search",
    "file-text",
    "briefcase",
    "graduation-cap",
    "scale",
    "heart-pulse",
    "calculator",
    "globe",
    "message-circle",
    "clipboard-list",
    "lightbulb",
    "shield-check",
    "chart-line",
    "users",
    "plane",
    "landmark",
]  # lucide-react icon names


class UserDoc(BaseModel):
    uid: str
    email: str | None
    display_name: str
    photo_url: str | None = None
    role: Role = "visitor"
    creator_profile: dict | None = None
    created_at: datetime
    updated_at: datetime


class StarterQuestion(BaseModel):
    label: str = Field(max_length=60)
    prompt: str = Field(max_length=300)


class Capability(BaseModel):
    icon: CapabilityIcon = "sparkles"
    name: str = Field(max_length=30)
    description: str = Field(max_length=120)


class Pricing(BaseModel):
    mode: PricingMode = "per_round"
    price_cents: int = Field(100, ge=0, le=100_000)  # forced to 0 when mode == "free"
    free_rounds: int = Field(0, ge=0, le=100)
    a2a_enabled: bool = True
    a2a_price_cents: int = Field(100, ge=0, le=100_000)


class RecordOption(BaseModel):
    key: str
    label: str = Field(max_length=40)


class RecordField(BaseModel):
    key: str
    label: str = Field(max_length=40)
    type: FieldType
    description: str = Field("", max_length=300)
    options: list[RecordOption] | None = None
    required: bool = False
    pii: bool = False
    show_in_table: bool = False
    min: float | None = None
    max: float | None = None


class RecordSchemaIn(BaseModel):  # request/wire model: [] allowed (= records disabled)
    version: Literal[1] = 1
    scope: Literal["session"] = "session"
    fields: list[RecordField] = Field(max_length=30)


class RecordSchema(RecordSchemaIn):  # stored/normalised model (output of records.validate_schema)
    fields: list[RecordField] = Field(min_length=1, max_length=30)


class AgentConfig(BaseModel):  # runtime config; changes need a new version
    soul: str = Field(max_length=6000)
    model: str = "gemini-3.8-flash"
    temperature: float | None = Field(None, ge=0, le=1.5)  # None = model default (§4.4)
    web_search_enabled: bool = True
    skill_ids: list[str] = Field(min_length=1, max_length=5)
    record_schema: RecordSchema | None = None
    # P2 guard (SPEC §18.2): Model Armor screening. Applies live (read from the agent doc on every turn
    # and call), so it is not part of AgentVersion and toggling it never sets config_dirty.
    safety_screening: bool = True


class AgentDoc(BaseModel):
    id: str
    owner_uid: str
    slug: str
    status: AgentStatus = "active"
    listed: bool = True
    deleted: bool = False
    title: str
    tagline: str = ""
    description: str = ""
    avatar_id: str | None = None
    greeting: str = ""
    input_placeholder: str = ""
    starter_questions: list[StarterQuestion] | None = None  # None = defaults, [] = none
    capabilities: list[Capability] = []
    accent_color: AccentColor = "blue"
    creator_name: str = ""
    creator_headline: str = ""
    pricing: Pricing
    config: AgentConfig
    config_dirty: bool = False  # config edited since active version was built
    active_version: int = 1
    version_count: int = 1
    knowledge_store: str | None = None
    knowledge_rev: int = 0  # Increment on knowledge status change/delete (§5.1, §9.1)
    created_at: datetime
    updated_at: datetime


class SkillFile(BaseModel):
    path: str
    size: int
    mime: str
    is_text: bool


class SkillRef(BaseModel):  # snapshot of an immutable skill
    skill_id: str
    slug: str
    name: str
    description: str
    gcs_prefix: str
    body_chars: int
    files: list[SkillFile]


class AgentVersion(BaseModel):
    n: int
    created_at: datetime
    created_by: str
    note: str = ""
    title: str  # snapshot (used in prompts / A2A card)
    soul: str
    model: str
    temperature: float | None
    web_search_enabled: bool
    skills: list[SkillRef]
    record_schema: RecordSchema | None
    knowledge_file_ids: list[str] = []  # informational snapshot (knowledge applies live, §10.1)


class VerifiedMandate(BaseModel):  # produced by a2a.mandates.verify_token
    mandate_id: str
    payer_uid: str
    audience: str
    intent: str
    max_amount_cents: int
    budget_cents: int
    expires_at: datetime
    kid: str
    payer_agent: str | None


# ---- Conversations and messages (written by chat, read by billing and records) ----
ToolKind = Literal["search", "web", "skill", "record", "a2a", "other"]
MessageStatus = Literal["ok", "failed", "aborted"]


class Citation(BaseModel):  # == TS Citation (§6.3)
    n: int
    kind: Literal["knowledge", "web"]
    title: str
    uri: str | None = None
    domain: str | None = None
    file_id: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    snippet: str | None = None


class ToolLineDoc(BaseModel):  # == TS ToolLine
    id: str
    tool: str
    kind: ToolKind
    title: str
    target: str = ""
    status: Literal["running", "completed", "error", "interrupted"]


class MessageCharge(BaseModel):
    charged_cents: int
    free: bool
    round_index: int | None


class MessageUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None


class MessageDoc(BaseModel):  # conversations/{cid}/messages/{id}
    id: str
    role: Literal["user", "model"]
    text: str
    status: MessageStatus
    turn_id: str
    request_id: str
    citations: list[Citation] = []
    search_entry_point_html: str | None = None
    tools: list[ToolLineDoc] = []
    usage: MessageUsage | None = None
    charge: MessageCharge | None = None
    error_code: str | None = None
    created_at: datetime


class ConversationDoc(BaseModel):  # conversations/{cid}
    id: str
    agent_id: str
    agent_slug: str
    owner_uid: str
    visitor_uid: str
    channel: Literal["web"] = "web"
    title: str
    rounds_ok: int = 0
    message_count: int = 0
    last_turn_id: str | None = None
    created_at: datetime
    updated_at: datetime


def iso_z(dt: datetime | None) -> str | None:
    """Wire form of a time: ISO-8601 UTC with ``Z`` (naive datetimes are taken as UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def message_to_wire(doc: MessageDoc) -> dict:
    """→ TS ``ChatMessage`` (drops request_id/usage/error_code, ISO times)."""
    return {
        "id": doc.id,
        "role": doc.role,
        "text": doc.text,
        "status": doc.status,
        "turn_id": doc.turn_id,
        "citations": [c.model_dump(mode="json") for c in doc.citations],
        "search_entry_point_html": doc.search_entry_point_html,
        "tools": [t.model_dump(mode="json") for t in doc.tools],
        "charge": doc.charge.model_dump(mode="json") if doc.charge is not None else None,
        "created_at": iso_z(doc.created_at),
    }


class HistoryItem(BaseModel):
    role: Literal["user", "model"]
    text: str


# ---- Cross-package value types (implemented by billing / knowledge / records, stubbed by testing.stubs) ----
@dataclass(frozen=True)
class RoundQuote:  # billing.preflight_round
    is_owner: bool
    round_index: int | None
    charge_cents: int
    free: bool
    balance_cents: int


@dataclass(frozen=True)
class RoundCharge:  # computed inside T2, passed to the MessageWriter
    request_id: str
    outcome: Literal["charged", "free", "not_counted", "duplicate"]
    charged_cents: int
    creator_share_cents: int
    round_index: int | None
    free_rounds_left: int | None
    balance_cents: int


# writer(tx, charge, conversation_exists) — must only WRITE (T2 already read the conversation doc)
MessageWriter = Callable[[AsyncTransaction, RoundCharge, bool], None]


@dataclass(frozen=True)
class A2ACharge:  # billing.settle_a2a_call
    call_id: str
    status: Literal["completed", "failed", "duplicate"]
    charged_cents: int
    creator_share_cents: int
    payer_balance_cents: int
    mandate_spent_cents: int


@dataclass(frozen=True)
class Passage:  # knowledge.search
    file_id: str
    filename: str
    text: str
    page_start: int | None
    page_end: int | None
    score: float | None


@dataclass(frozen=True)
class SaveResult:  # records.apply_values
    ok: bool
    saved: int
    completed: bool
    missing_required: list[str]
    error: str | None


class CitationCollector(Protocol):  # implemented by knowledge.citations.Collector
    def add_passages(self, passages: list[Passage]) -> list[int]: ...
    def add_grounding(self, gm: types.GroundingMetadata | None) -> list[str]: ...
    def sources(self) -> list[Citation]: ...
    @property
    def search_entry_point_html(self) -> str | None: ...
    @property
    def web_search_queries(self) -> list[str]: ...
