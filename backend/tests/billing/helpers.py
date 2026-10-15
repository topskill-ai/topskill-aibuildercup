"""Helpers shared by billing tests: a contract-exact chat writer, mandates and small readers."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from google.cloud import firestore

from topskill.auth import CurrentUser
from topskill.db import Col, get_db, new_id, utcnow
from topskill.domain import (
    AgentDoc,
    ConversationDoc,
    MessageCharge,
    MessageDoc,
    RoundCharge,
    VerifiedMandate,
)


def new_request_id() -> str:
    return f"turn_{secrets.token_hex(16)}"


class Writer:
    """A chat writer following the §8.5 contract (writes only); records every call it receives."""

    def __init__(
        self,
        agent: AgentDoc,
        visitor: CurrentUser,
        conversation_id: str,
        request_id: str,
        user_text: str = "Hello there",
        model_text: str = "Hi!",
    ) -> None:
        self.agent = agent
        self.visitor = visitor
        self.cid = conversation_id
        self.request_id = request_id
        self.user_text = user_text
        self.model_text = model_text
        self.calls: list[tuple[RoundCharge, bool]] = []

    def __call__(self, tx: Any, charge: RoundCharge, conversation_exists: bool) -> None:
        self.calls.append((charge, conversation_exists))
        db = get_db()
        conv_ref = db.collection(Col.CONVERSATIONS).document(self.cid)
        now = utcnow()
        succeeded = charge.outcome in ("charged", "free")
        status = "ok" if succeeded else "failed"
        msg_charge = (
            MessageCharge(
                charged_cents=charge.charged_cents,
                free=charge.charged_cents == 0,
                round_index=charge.round_index,
            )
            if succeeded
            else None
        )
        for role, text in (("user", self.user_text), ("model", self.model_text)):
            msg = MessageDoc(
                id=("u_" if role == "user" else "m_") + self.request_id,
                role=role,  # type: ignore[arg-type]
                text=text,
                status=status,  # type: ignore[arg-type]
                turn_id="t" + self.request_id[-8:],
                request_id=self.request_id,
                charge=msg_charge if role == "model" else None,
                created_at=now,
            )
            tx.set(conv_ref.collection(Col.MESSAGES).document(msg.id), msg.model_dump(mode="python"))
        if not conversation_exists:
            tx.create(
                conv_ref,
                ConversationDoc(
                    id=self.cid,
                    agent_id=self.agent.id,
                    agent_slug=self.agent.slug,
                    owner_uid=self.agent.owner_uid,
                    visitor_uid=self.visitor.uid,
                    title=self.user_text[:80],
                    rounds_ok=1 if succeeded else 0,
                    message_count=2,
                    last_turn_id="t" + self.request_id[-8:],
                    created_at=now,
                    updated_at=now,
                ).model_dump(mode="python"),
            )
        else:
            tx.update(
                conv_ref,
                {
                    "rounds_ok": firestore.Increment(1 if succeeded else 0),
                    "message_count": firestore.Increment(2),
                    "last_turn_id": "t" + self.request_id[-8:],
                    "updated_at": now,
                },
            )


async def settle(
    billing: Any,
    agent: AgentDoc,
    payer: CurrentUser,
    *,
    conversation_id: str | None = None,
    request_id: str | None = None,
    succeeded: bool = True,
    user_text: str = "Hello there",
    model: str | None = "gemini-3.8-flash",
) -> tuple[RoundCharge, Writer]:
    cid = conversation_id or new_id("cnv")
    rid = request_id or new_request_id()
    writer = Writer(agent, payer, cid, rid, user_text=user_text)
    charge = await billing.settle_round(
        agent=agent,
        payer=payer,
        conversation_id=cid,
        request_id=rid,
        succeeded=succeeded,
        model=model,
        write_messages=writer,
    )
    return charge, writer


async def make_mandate(
    payer: CurrentUser,
    *,
    max_amount_cents: int = 500,
    budget_cents: int = 2000,
    audience: str = "*",
    spent_cents: int = 0,
    reserved_cents: int = 0,
    status: str = "active",
) -> VerifiedMandate:
    """``mandates/{id}`` with the §5.1 shape (as a2a's POST /api/mandates would create it)."""
    mid = new_id("mdt")
    now = utcnow()
    expires = now + timedelta(minutes=30)
    await (
        get_db()
        .collection(Col.MANDATES)
        .document(mid)
        .set(
            {
                "id": mid,
                "payer_uid": payer.uid,
                "audience": audience,
                "intent": "Research visa options for a new hire",
                "payer_agent": "enterprise-assistant",
                "max_amount_cents": max_amount_cents,
                "budget_cents": budget_cents,
                "spent_cents": spent_cents,
                "calls": 0,
                "reserved_cents": reserved_cents,
                "status": status,
                "kid": "dev-1",
                "issued_at": now,
                "expires_at": expires,
            }
        )
    )
    return VerifiedMandate(
        mandate_id=mid,
        payer_uid=payer.uid,
        audience=audience,
        intent="Research visa options for a new hire",
        max_amount_cents=max_amount_cents,
        budget_cents=budget_cents,
        expires_at=expires,
        kid="dev-1",
        payer_agent="enterprise-assistant",
    )


async def doc(collection: str, doc_id: str) -> dict | None:
    snap = await get_db().collection(collection).document(doc_id).get()
    return snap.to_dict() if snap.exists else None


async def balance(uid: str) -> int | None:
    data = await doc(Col.WALLETS, uid)
    return None if data is None else int(data["balance_cents"])


async def ledger(uid: str) -> dict[str, dict]:
    """All ledger rows of ``uid`` keyed by doc id (single-field query)."""
    q = get_db().collection(Col.LEDGER).where(filter=firestore.FieldFilter("uid", "==", uid))
    return {snap.id: snap.to_dict() async for snap in q.stream()}


async def stats(agent_id: str) -> dict:
    return await doc(Col.AGENT_STATS, agent_id) or {}
