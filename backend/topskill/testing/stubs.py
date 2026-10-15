"""Signature-exact stand-ins for billing, knowledge and records (SPEC §14.3).

Typed only with ``topskill.domain`` types so they never import a domain package. ``chat/deps.py`` picks
the real package when it imports and falls back to these instances otherwise; nothing else imports them.
Every stub class has ``is_stub = True`` so callers can skip optional work (e.g. record extraction).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import urlparse

from google.adk.tools import BaseTool, FunctionTool
from google.cloud import firestore
from google.genai import types

from topskill.auth import CurrentUser
from topskill.config import get_settings
from topskill.db import Col, get_db
from topskill.domain import (
    A2ACharge,
    AgentDoc,
    AgentVersion,
    Citation,
    CitationCollector,
    MessageWriter,
    Passage,
    RecordSchema,
    RecordSchemaIn,
    RoundCharge,
    RoundQuote,
    SaveResult,
    VerifiedMandate,
)
from topskill.errors import ApiError, Codes

# --------------------------------------------------------------------------- billing


class StubBilling:
    """Everything is free; ``settle_round`` still runs a real transaction around the chat writer."""

    is_stub = True

    async def get_or_open_wallet(self, uid: str) -> int:
        return 0

    async def get_balance(self, uid: str) -> int:
        return 0

    def creator_share(self, amount_cents: int) -> int:
        return amount_cents * get_settings().creator_revenue_share_bp // 10000

    async def preflight_round(self, agent: AgentDoc, payer: CurrentUser) -> RoundQuote:
        if agent.deleted or agent.status != "active":
            raise ApiError(
                410, Codes.AGENT_OFFLINE, "This agent is temporarily unavailable.", {"slug": agent.slug}
            )
        return RoundQuote(
            is_owner=payer.uid == agent.owner_uid,
            round_index=None,
            charge_cents=0,
            free=True,
            balance_cents=0,
        )

    async def billing_status(self, agent: AgentDoc, user: CurrentUser) -> dict:
        return {
            "billing_mode": agent.pricing.mode,
            "price_cents": agent.pricing.price_cents,
            "free_rounds": agent.pricing.free_rounds,
            "is_owner": user.uid == agent.owner_uid,
            "used_rounds": 0,
            "free_rounds_left": None,
            "next_round_charge_cents": 0,
            "balance_cents": 0,
            "can_afford_next_round": True,
            "owner_available": True,
        }

    async def settle_round(
        self,
        *,
        agent: AgentDoc,
        payer: CurrentUser,
        conversation_id: str,
        request_id: str,
        succeeded: bool,
        model: str | None,
        write_messages: MessageWriter,
    ) -> RoundCharge:
        db = get_db()
        conv_ref = db.collection(Col.CONVERSATIONS).document(conversation_id)
        charge = RoundCharge(
            request_id=request_id,
            outcome="free" if succeeded else "not_counted",
            charged_cents=0,
            creator_share_cents=0,
            round_index=None,
            free_rounds_left=None,
            balance_cents=0,
        )

        @firestore.async_transactional
        async def _settle(tx: Any) -> RoundCharge:
            conv = await conv_ref.get(transaction=tx)
            write_messages(tx, charge, conv.exists)
            return charge

        return await _settle(db.transaction())

    async def preflight_a2a(self, agent: AgentDoc, mandate: VerifiedMandate) -> int:
        return agent.pricing.a2a_price_cents

    async def settle_a2a_call(
        self,
        *,
        call_id: str,
        succeeded: bool,
        model: str | None,
        answer_preview: str,
        error_code: str | None = None,
    ) -> A2ACharge:
        return A2ACharge(
            call_id=call_id,
            status="completed" if succeeded else "failed",
            charged_cents=0,
            creator_share_cents=0,
            payer_balance_cents=0,
            mandate_spent_cents=0,
        )


# --------------------------------------------------------------------------- knowledge


def _passage_key(p: Passage) -> tuple[str, int | None, str]:
    return (p.file_id, p.page_start, p.text[:80])


class StubCollector:
    """Minimal in-memory ``CitationCollector`` (stable passage numbers, web chunks deduped by uri)."""

    def __init__(self) -> None:
        self._knowledge: dict[tuple[str, int | None, str], Citation] = {}
        self._web: dict[str, Citation] = {}
        self._queries: list[str] = []
        self._entry_point: str | None = None

    def _next_n(self) -> int:
        return len(self._knowledge) + len(self._web) + 1

    def add_passages(self, passages: list[Passage]) -> list[int]:
        numbers: list[int] = []
        for p in passages:
            key = _passage_key(p)
            if key not in self._knowledge:
                self._knowledge[key] = Citation(
                    n=self._next_n(),
                    kind="knowledge",
                    title=p.filename,
                    file_id=p.file_id,
                    page_start=p.page_start,
                    page_end=p.page_end,
                    snippet=p.text[:300],
                )
            numbers.append(self._knowledge[key].n)
        return numbers

    def add_grounding(self, gm: types.GroundingMetadata | None) -> list[str]:
        if gm is None:
            return []
        new_queries: list[str] = []
        for query in gm.web_search_queries or []:
            if query and query not in self._queries:
                self._queries.append(query)
                new_queries.append(query)
        for chunk in gm.grounding_chunks or []:
            web = chunk.web
            if web is None or not web.uri or web.uri in self._web:
                continue
            self._web[web.uri] = Citation(
                n=self._next_n(),
                kind="web",
                title=web.title or web.domain or urlparse(web.uri).netloc,
                uri=web.uri,
                domain=web.domain or web.title,
            )
        if gm.search_entry_point and gm.search_entry_point.rendered_content:
            self._entry_point = gm.search_entry_point.rendered_content
        return new_queries

    def sources(self) -> list[Citation]:
        return sorted(
            [*self._knowledge.values(), *self._web.values()], key=lambda c: (c.kind != "knowledge", c.n)
        )

    @property
    def search_entry_point_html(self) -> str | None:
        return self._entry_point

    @property
    def web_search_queries(self) -> list[str]:
        return list(self._queries)


class StubKnowledge:
    """No documents: nothing is ready, search returns nothing."""

    is_stub = True
    Collector = StubCollector

    async def ensure_store(self, agent_id: str) -> str:
        return "local"

    async def has_ready_files(self, agent_id: str) -> bool:
        return False

    async def search(self, agent_id: str, query: str, top_k: int | None = None) -> list[Passage]:
        return []

    async def ingest_file(self, agent_id: str, file_id: str) -> None:
        return None

    async def delete_file(self, agent_id: str, file_id: str) -> None:
        return None

    def new_collector(self) -> CitationCollector:
        return StubCollector()

    def make_search_tool(self, agent_id: str, collector: CitationCollector | None) -> FunctionTool:
        async def search_knowledge(query: str) -> dict:
            """Search the creator's reference documents. Use for any question that may be covered by them.
            Cite results inline as [n]."""
            return {
                "results": [],
                "note": "Cite as [n]. If nothing relevant, say the documents don't cover it.",
            }

        return FunctionTool(search_knowledge)


# --------------------------------------------------------------------------- records


class StubRecords:
    """Schemas pass through; nothing is ever saved."""

    is_stub = True

    def validate_schema(
        self, raw: RecordSchemaIn | dict | None, *, lenient: bool = False
    ) -> RecordSchema | None:
        if raw is None:
            return None
        parsed = raw if isinstance(raw, RecordSchemaIn) else RecordSchemaIn.model_validate(raw)
        if not parsed.fields:
            return None
        return RecordSchema.model_validate(parsed.model_dump())

    async def assert_schema_change_allowed(
        self, agent_id: str, old: RecordSchema | None, new: RecordSchema | None
    ) -> None:
        return None

    async def prompt_section(self, schema: RecordSchema, agent_id: str, conversation_id: str) -> str:
        return ""

    async def apply_values(
        self,
        *,
        agent: AgentDoc,
        version_n: int,
        conversation_id: str,
        visitor: CurrentUser,
        fields: dict,
        complete: bool | None,
        summary: str | None,
        source: Literal["tool", "extraction"],
        only_empty: bool = False,
    ) -> SaveResult:
        return SaveResult(ok=True, saved=0, completed=False, missing_required=[], error=None)

    def make_save_record_tool(
        self,
        *,
        schema: RecordSchema,
        agent: AgentDoc,
        version_n: int,
        conversation_id: str,
        visitor: CurrentUser,
        on_saved: Callable[[SaveResult], None],
    ) -> BaseTool:
        def save_record(fields: dict[str, Any], complete: bool = False, summary: str = "") -> dict:
            """Save what the visitor explicitly told you into the structured record."""
            on_saved(SaveResult(ok=True, saved=0, completed=False, missing_required=[], error=None))
            return {"ok": True, "saved": 0}

        return FunctionTool(save_record)

    async def extract(
        self, *, agent: AgentDoc, version: AgentVersion, conversation_id: str, visitor: CurrentUser
    ) -> SaveResult | None:
        return None


stub_billing = StubBilling()
stub_knowledge = StubKnowledge()
stub_records = StubRecords()
