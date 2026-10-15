"""Pydantic mirrors of every SSE payload in SPEC §6.3 (``ChatStreamEvent`` / ``AssistantStreamEvent``).

Emit only via ``topskill.sse.sse_event(name, Model)``. Field names and nullability match the TypeScript
types exactly; every field is required on the wire (nullable ones are sent as ``null``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from topskill.domain import Citation, ToolKind


class EventName:
    STATUS = "status"
    TEXT_DELTA = "text_delta"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    CITATIONS = "citations"
    ERROR = "error"
    DONE = "done"
    A2A_DISCOVER = "a2a_discover"
    A2A_CALL = "a2a_call"
    A2A_RESULT = "a2a_result"


class StatusData(BaseModel):
    state: Literal["ready"] = "ready"
    conversation_id: str | None
    turn_id: str
    request_id: str
    model: str


class TextDeltaData(BaseModel):
    delta: str


class ToolStartData(BaseModel):
    id: str
    tool: str
    kind: ToolKind
    title: str
    target: str


class ToolEndData(BaseModel):
    id: str
    tool: str
    status: Literal["completed", "error"]


class CitationsData(BaseModel):
    sources: list[Citation]
    search_entry_point_html: str | None
    web_search_queries: list[str]


class StreamErrorData(BaseModel):
    code: str
    message: str
    trace_id: str


class DoneUsage(BaseModel):
    request_id: str
    input_tokens: int | None
    output_tokens: int | None
    model: str | None


class DoneBilling(BaseModel):
    charged_cents: int
    free: bool
    round_index: int | None
    free_rounds_left: int | None
    balance_cents: int


class DoneData(BaseModel):
    conversation_id: str | None
    message_id: str | None
    turn_id: str
    outcome: Literal["ok", "failed", "aborted"]
    usage: DoneUsage | None
    billing: DoneBilling | None


class DiscoveredAgent(BaseModel):
    slug: str
    name: str
    tagline: str
    price_cents: int
    card_url: str


class A2ADiscoverData(BaseModel):
    query: str
    agents: list[DiscoveredAgent]


class A2ACallData(BaseModel):
    call_id: str | None
    slug: str
    agent_name: str
    endpoint: str
    price_cents: int
    request_preview: str


class A2AResultData(BaseModel):
    call_id: str | None
    slug: str
    status: Literal["completed", "failed", "rejected"]
    charged_cents: int
    creator_share_cents: int
    answer_preview: str
    error_code: str | None
    payer_balance_cents: int
    mandate_spent_cents: int
    mandate_budget_cents: int


class MandateCounters(BaseModel):
    id: str
    spent_cents: int
    budget_cents: int
    calls: int


class AssistantDoneData(DoneData):
    billing: None = None
    mandate: MandateCounters
