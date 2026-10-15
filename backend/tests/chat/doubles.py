"""Test doubles for billing/knowledge/records injected through ``chat.deps.set_deps``.

They keep the §14.2 signatures, so the chat runtime is exercised exactly as with the real packages.
``ChargingBilling.settle_round`` runs a real Firestore transaction around the chat writer (like T2) and
charges ``pricing.price_cents`` for every successful non-owner round.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from google.adk.tools import BaseTool, FunctionTool
from google.cloud import firestore

from topskill.auth import CurrentUser
from topskill.db import Col, get_db
from topskill.domain import (
    AgentDoc,
    AgentVersion,
    CitationCollector,
    MessageWriter,
    Passage,
    RecordSchema,
    RoundCharge,
    RoundQuote,
    SaveResult,
)
from topskill.errors import ApiError
from topskill.testing.stubs import StubBilling, StubCollector, StubKnowledge, StubRecords


@dataclass
class SettleCall:
    conversation_id: str
    request_id: str
    succeeded: bool
    model: str | None
    charge: RoundCharge | None = None


class ChargingBilling(StubBilling):
    is_stub = False

    def __init__(self) -> None:
        self.settles: list[SettleCall] = []
        self.preflights: list[str] = []
        self.preflight_error: ApiError | None = None
        self.fail_settlement = False
        self.before_settle: Callable[[str], None] | None = None

    async def preflight_round(self, agent: AgentDoc, payer: CurrentUser) -> RoundQuote:
        self.preflights.append(payer.uid)
        if self.preflight_error is not None:
            raise self.preflight_error
        return await super().preflight_round(agent, payer)

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
        call = SettleCall(conversation_id, request_id, succeeded, model)
        self.settles.append(call)
        if self.before_settle is not None:
            self.before_settle(request_id)
        if self.fail_settlement:
            raise RuntimeError("settlement down")
        db = get_db()
        conv_ref = db.collection(Col.CONVERSATIONS).document(conversation_id)
        msg_ref = conv_ref.collection(Col.MESSAGES).document(f"m_{request_id}")
        price = agent.pricing.price_cents if agent.pricing.mode == "per_round" else 0

        @firestore.async_transactional
        async def _settle(tx: Any) -> RoundCharge:
            msg = await msg_ref.get(transaction=tx)
            conv = await conv_ref.get(transaction=tx)
            if msg.exists:
                stored = (msg.to_dict() or {}).get("charge") or {}
                return RoundCharge(
                    request_id,
                    "duplicate",
                    stored.get("charged_cents", 0),
                    0,
                    stored.get("round_index"),
                    None,
                    0,
                )
            if not succeeded:
                charge = RoundCharge(request_id, "not_counted", 0, 0, None, None, 9_000)
            elif payer.uid == agent.owner_uid:
                charge = RoundCharge(request_id, "free", 0, 0, None, None, 9_000)
            else:
                rounds = (conv.to_dict() or {}).get("rounds_ok", 0) if conv.exists else 0
                charge = RoundCharge(
                    request_id, "charged", price, price * 8 // 10, rounds + 1, 0, 9_000 - price
                )
            write_messages(tx, charge, conv.exists)
            return charge

        call.charge = await _settle(db.transaction())
        return call.charge


class FakeKnowledge(StubKnowledge):
    is_stub = False

    def __init__(self, passages: list[Passage] | None = None) -> None:
        self.passages = passages or []
        self.queries: list[str] = []
        self.collectors: list[CitationCollector | None] = []

    async def has_ready_files(self, agent_id: str) -> bool:
        return bool(self.passages)

    def new_collector(self) -> CitationCollector:
        return StubCollector()

    def make_search_tool(self, agent_id: str, collector: CitationCollector | None) -> FunctionTool:
        self.collectors.append(collector)
        passages = self.passages
        queries = self.queries

        async def search_knowledge(query: str) -> dict:
            """Search the creator's reference documents. Cite results inline as [n]."""
            queries.append(query)
            numbers = collector.add_passages(passages) if collector else list(range(1, len(passages) + 1))
            return {
                "results": [
                    {"ref": f"[{n}]", "source": p.filename, "pages": str(p.page_start), "text": p.text}
                    for n, p in zip(numbers, passages, strict=True)
                ],
                "note": "Cite as [n].",
            }

        return FunctionTool(search_knowledge)


@dataclass
class RecordsCalls:
    saved: list[dict] = field(default_factory=list)
    extracted: list[str] = field(default_factory=list)


class FakeRecords(StubRecords):
    is_stub = False

    def __init__(self) -> None:
        self.calls = RecordsCalls()

    async def prompt_section(self, schema: RecordSchema, agent_id: str, conversation_id: str) -> str:
        keys = ", ".join(f.key for f in schema.fields)
        return f"## Notes to collect\nFields: {keys}"

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
        calls = self.calls

        def save_record(fields: dict[str, Any], complete: bool = False, summary: str = "") -> dict:
            """Save what the visitor explicitly said."""
            calls.saved.append(fields)
            result = SaveResult(
                ok=True, saved=len(fields), completed=complete, missing_required=[], error=None
            )
            on_saved(result)
            return {"ok": True, "saved": len(fields), "missing_required": []}

        return FunctionTool(save_record)

    async def extract(
        self, *, agent: AgentDoc, version: AgentVersion, conversation_id: str, visitor: CurrentUser
    ) -> SaveResult | None:
        self.calls.extracted.append(conversation_id)
        return None
