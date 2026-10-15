"""Test-only stand-ins for the a2a lane: a scripted ADK model and a Firestore-backed billing double.

The real ``topskill.billing`` / ``topskill.chat`` packages land in other lanes; these doubles implement
exactly the §14.2 signatures the a2a code uses (``preflight_a2a``, ``settle_a2a_call`` with T3 semantics,
``get_balance``) so the a2a tests exercise reservation, release and charging end to end.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.cloud import firestore
from google.genai import types
from pydantic import ConfigDict, Field

from topskill.auth import CurrentUser
from topskill.db import Col, get_db, utcnow
from topskill.domain import A2ACharge, AgentDoc, AgentVersion, VerifiedMandate
from topskill.errors import ApiError, Codes

Step = Callable[[LlmRequest], LlmResponse | BaseException] | LlmResponse | BaseException


def text_response(text: str) -> LlmResponse:
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        model_version="scripted-model",
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=10, candidates_token_count=5
        ),
    )


def call_response(name: str, args: dict) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]
        ),
        model_version="scripted-model",
    )


def function_responses(request: LlmRequest) -> list[types.FunctionResponse]:
    """Every function response in the request contents, oldest first."""
    out: list[types.FunctionResponse] = []
    for content in request.contents or []:
        for part in content.parts or []:
            if part.function_response is not None:
                out.append(part.function_response)
    return out


class ScriptedLlm(BaseLlm):
    """Returns the next scripted step per ``generate_content_async`` call (the last step repeats).
    A step is an ``LlmResponse``, an exception to raise, or a callable of the request returning either."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    model: str = "scripted-model"
    steps: list[Any] = Field(default_factory=list)
    requests: list[Any] = Field(default_factory=list)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request)
        step = self.steps[min(len(self.requests) - 1, len(self.steps) - 1)]
        if callable(step) and not isinstance(step, LlmResponse):
            step = step(llm_request)
            if inspect.isawaitable(step):
                step = await step
        if isinstance(step, BaseException):
            raise step
        yield step


@dataclass
class RuntimeFactory:
    """Replacement for ``chat.build_runtime_agent``: one ``ScriptedLlm`` script per agent slug."""

    scripts: dict[str, list[Any]] = field(default_factory=dict)
    default: list[Any] = field(
        default_factory=lambda: [text_response("Here is the answer [1].\n\nSources: none")]
    )
    built: list[str] = field(default_factory=list)
    models: dict[str, ScriptedLlm] = field(default_factory=dict)

    async def __call__(self, agent: AgentDoc, version: AgentVersion) -> LlmAgent:
        self.built.append(agent.slug)
        llm = ScriptedLlm(steps=list(self.scripts.get(agent.slug, self.default)))
        self.models[agent.slug] = llm
        return LlmAgent(
            name="agent_" + agent.slug.replace("-", "_")[:50],
            model=llm,
            instruction=f"You are {version.title}.",
        )


class FakeBilling:
    """Billing double with the §8.6 / T3 semantics the a2a lane depends on (ledger rows omitted)."""

    is_stub = False

    def __init__(self, share_bp: int = 8000) -> None:
        self.share_bp = share_bp
        self.settle_calls: list[dict] = []

    async def get_balance(self, uid: str) -> int:
        snap = await get_db().collection(Col.WALLETS).document(uid).get()
        return int((snap.to_dict() or {}).get("balance_cents", 0)) if snap.exists else 0

    async def get_or_open_wallet(self, uid: str) -> int:
        return await self.get_balance(uid)

    def creator_share(self, amount_cents: int) -> int:
        return amount_cents * self.share_bp // 10000

    async def preflight_a2a(self, agent: AgentDoc, mandate: VerifiedMandate) -> int:
        price = 0 if mandate.payer_uid == agent.owner_uid else agent.pricing.a2a_price_cents
        if price > mandate.max_amount_cents:
            raise ApiError(402, Codes.MANDATE_EXCEEDS_MAX, "Over the per-call cap.")
        snap = await get_db().collection(Col.MANDATES).document(mandate.mandate_id).get()
        data = snap.to_dict() or {}
        if int(data.get("spent_cents", 0)) + int(data.get("reserved_cents", 0)) + price > int(
            data.get("budget_cents", 0)
        ):
            raise ApiError(402, Codes.MANDATE_BUDGET_EXHAUSTED, "Budget used up.")
        if await self.get_balance(mandate.payer_uid) < price:
            raise ApiError(402, Codes.PAYER_INSUFFICIENT_POINTS, "Not enough credits.")
        if await self.get_balance(agent.owner_uid) <= 0:
            raise ApiError(402, Codes.INSUFFICIENT_POINTS, "Agent unavailable.", {"reason": "owner"})
        return price

    async def settle_a2a_call(
        self,
        *,
        call_id: str,
        succeeded: bool,
        model: str | None,
        answer_preview: str,
        error_code: str | None = None,
    ) -> A2ACharge:
        self.settle_calls.append({"call_id": call_id, "succeeded": succeeded, "error_code": error_code})
        db = get_db()
        call_ref = db.collection(Col.A2A_CALLS).document(call_id)

        @firestore.async_transactional
        async def _settle(tx: Any) -> A2ACharge:
            call = (await call_ref.get(transaction=tx)).to_dict()
            assert call is not None, f"unknown call {call_id}"
            mandate_ref = db.collection(Col.MANDATES).document(call["mandate_id"])
            mandate = (await mandate_ref.get(transaction=tx)).to_dict() or {}
            payer_ref = db.collection(Col.WALLETS).document(call["payer_uid"])
            owner_ref = db.collection(Col.WALLETS).document(call["owner_uid"])
            payer = (await payer_ref.get(transaction=tx)).to_dict() or {}
            owner = (await owner_ref.get(transaction=tx)).to_dict() or {}
            if call["status"] != "running":
                return A2ACharge(
                    call_id=call_id,
                    status="duplicate",
                    charged_cents=int(call.get("charged_cents", 0)),
                    creator_share_cents=int(call.get("creator_share_cents", 0)),
                    payer_balance_cents=int(payer.get("balance_cents", 0)),
                    mandate_spent_cents=int(mandate.get("spent_cents", 0)),
                )
            price = int(call["price_cents"])
            now = utcnow()
            spent = int(mandate.get("spent_cents", 0))
            reserved = int(mandate.get("reserved_cents", 0)) - price
            payer_balance = int(payer.get("balance_cents", 0))
            if not succeeded:
                tx.update(mandate_ref, {"reserved_cents": reserved})
                tx.update(call_ref, {"status": "failed", "error_code": error_code, "finished_at": now})
                return A2ACharge(call_id, "failed", 0, 0, payer_balance, spent)
            charged = max(0, min(price, payer_balance, int(mandate.get("budget_cents", 0)) - spent))
            share = self.creator_share(charged) if call["payer_uid"] != call["owner_uid"] else 0
            tx.update(
                mandate_ref,
                {
                    "reserved_cents": reserved,
                    "spent_cents": spent + charged,
                    "calls": int(mandate.get("calls", 0)) + 1,
                },
            )
            tx.update(payer_ref, {"balance_cents": payer_balance - charged})
            if call["payer_uid"] != call["owner_uid"]:
                tx.set(owner_ref, {"balance_cents": int(owner.get("balance_cents", 0)) + share}, merge=True)
            tx.update(
                call_ref,
                {
                    "status": "completed",
                    "charged_cents": charged,
                    "creator_share_cents": share,
                    "model": model,
                    "answer_preview": answer_preview[:300],
                    "finished_at": now,
                },
            )
            return A2ACharge(call_id, "completed", charged, share, payer_balance - charged, spent + charged)

        return await _settle(db.transaction(max_attempts=15))


@dataclass
class FakeDeps:
    billing: Any
    knowledge: Any = None
    records: Any = None


async def payer_user(uid: str) -> CurrentUser:
    from topskill.testing.factories import make_user

    return await make_user("visitor", uid=uid)
