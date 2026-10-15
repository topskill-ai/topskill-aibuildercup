"""One voice round = one visitor utterance and the spoken answer to it (SPEC §18.1 "Rounds").

A round collects the visitor's input transcription, the model's output transcription and audio, tool
lines, citations and usage. Its outcome follows the text chat rules exactly
(:func:`topskill.chat.runtime.classify_outcome`): ``aborted`` when the visitor barged in (or hung up)
before the model finished the answer, ``failed`` on an error or an answer without text, else ``ok`` (a
counted, possibly charged round). An interruption that arrives after the model finished does not change
an ``ok`` round (SPEC §7.4).

Persisted like a text turn: ``u_{request_id}`` + ``m_{request_id}`` written by :func:`round_writer`
inside billing transaction T2. Voice rounds are recognisable by the ``voice_`` prefix of their
``turn_id`` / ``request_id``.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime

from google.cloud import firestore

from topskill.auth import CurrentUser
from topskill.chat.runtime import Outcome, classify_outcome, conversation_title
from topskill.db import Col, get_db, utcnow
from topskill.domain import (
    AgentDoc,
    CitationCollector,
    ConversationDoc,
    MessageCharge,
    MessageDoc,
    MessageUsage,
    RoundCharge,
    ToolLineDoc,
)

VOICE_PREFIX = "voice_"
EMPTY_USER_TEXT = "(spoken message)"


def new_voice_ids() -> tuple[str, str]:
    """``(turn_id, request_id)`` for a new round, both prefixed ``voice_``."""
    return VOICE_PREFIX + secrets.token_hex(8), VOICE_PREFIX + secrets.token_hex(16)


def is_voice_turn(turn_id: str) -> bool:
    return turn_id.startswith(VOICE_PREFIX)


def join_captions(parts: list[str]) -> str:
    """Concatenate transcription chunks. Chunks carry their own spacing, except that the input
    transcription sometimes starts a new sentence without a space ("…need forPlease…")."""
    out = ""
    for part in parts:
        if not part:
            continue
        if (
            out
            and not out[-1].isspace()
            and not part[0].isspace()
            and out[-1].isalpha()
            and part[0].isupper()
        ):
            out += " "
        out += part
    return " ".join(out.split())


@dataclass
class Round:
    collector: CitationCollector
    model: str
    turn_id: str = ""
    request_id: str = ""
    started_at: datetime = field(default_factory=utcnow)
    user_parts: list[str] = field(default_factory=list)
    model_parts: list[str] = field(default_factory=list)
    audio_bytes: int = 0
    tools: dict[str, ToolLineDoc] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    interrupted: bool = False  # barge-in or hang-up before the answer was complete
    error_code: str | None = None
    tool_impls: dict | None = None  # the round's ADK tools, built on the first tool call
    tool_calls: int = 0  # tools executed in this round (VOICE_MAX_TOOL_CALLS_PER_ROUND)
    tool_refusals: int = 0  # calls refused because the limit was reached

    def __post_init__(self) -> None:
        if not self.turn_id or not self.request_id:
            self.turn_id, self.request_id = new_voice_ids()

    # ---- accumulation
    def add_usage(self, prompt_tokens: int | None, response_tokens: int | None) -> None:
        if prompt_tokens is not None:
            self.input_tokens = (self.input_tokens or 0) + prompt_tokens
        if response_tokens is not None:
            self.output_tokens = (self.output_tokens or 0) + response_tokens

    # ---- views
    @property
    def user_text(self) -> str:
        return join_captions(self.user_parts)

    @property
    def model_text(self) -> str:
        return "".join(self.model_parts).strip()

    @property
    def answered(self) -> bool:
        """The model produced something audible or readable for this round."""
        return self.audio_bytes > 0 or bool(self.model_text)

    @property
    def worth_saving(self) -> bool:
        """Persist rounds the visitor saw something of; an utterance without any reply is dropped."""
        return self.answered or bool(self.tools) or (self.error_code is not None and bool(self.user_text))

    def outcome(self) -> Outcome:
        return classify_outcome(
            abort_requested=self.interrupted,
            runner_finished=not self.interrupted,
            answer_text=self.model_text,
            turn_error=self.error_code,
        )

    def tool_lines(self, outcome: Outcome) -> list[ToolLineDoc]:
        """Tool lines with every still-running line closed (interrupted when the round was)."""
        closed = "interrupted" if outcome == "aborted" else "completed"
        return [
            t if t.status != "running" else t.model_copy(update={"status": closed})
            for t in self.tools.values()
        ]

    def usage(self) -> MessageUsage:
        return MessageUsage(
            input_tokens=self.input_tokens, output_tokens=self.output_tokens, model=self.model
        )


def round_writer(
    rnd: Round,
    *,
    agent: AgentDoc,
    visitor: CurrentUser,
    conversation_id: str,
    outcome: Outcome,
):
    """``MessageWriter`` of SPEC §8.5 for a voice round (writes only; T2 already read the conversation)."""
    succeeded = outcome == "ok"

    def write(tx, charge: RoundCharge, conversation_exists: bool) -> None:
        now = utcnow()
        conv_ref = get_db().collection(Col.CONVERSATIONS).document(conversation_id)
        messages = conv_ref.collection(Col.MESSAGES)
        msg_charge = (
            MessageCharge(
                charged_cents=charge.charged_cents,
                free=charge.charged_cents == 0,
                round_index=charge.round_index,
            )
            if charge.outcome in ("charged", "free")
            else None
        )
        user_text = rnd.user_text or EMPTY_USER_TEXT
        user_msg = MessageDoc(
            id=f"u_{rnd.request_id}",
            role="user",
            text=user_text,
            status="ok" if succeeded else outcome,
            turn_id=rnd.turn_id,
            request_id=rnd.request_id,
            created_at=rnd.started_at,
        )
        model_msg = MessageDoc(
            id=f"m_{rnd.request_id}",
            role="model",
            text=rnd.model_text,
            status=outcome,
            turn_id=rnd.turn_id,
            request_id=rnd.request_id,
            citations=rnd.collector.sources(),
            search_entry_point_html=rnd.collector.search_entry_point_html,
            tools=rnd.tool_lines(outcome),
            usage=rnd.usage(),
            charge=msg_charge,
            error_code=rnd.error_code,
            created_at=max(now, rnd.started_at),
        )
        tx.set(messages.document(user_msg.id), user_msg.model_dump(mode="python"))
        tx.set(messages.document(model_msg.id), model_msg.model_dump(mode="python"))
        if not conversation_exists:
            conv = ConversationDoc(
                id=conversation_id,
                agent_id=agent.id,
                agent_slug=agent.slug,
                owner_uid=agent.owner_uid,
                visitor_uid=visitor.uid,
                title=conversation_title(user_text),
                rounds_ok=1 if succeeded else 0,
                message_count=2,
                last_turn_id=rnd.turn_id,
                created_at=now,
                updated_at=now,
            )
            tx.create(conv_ref, conv.model_dump(mode="python"))
        else:
            tx.update(
                conv_ref,
                {
                    "rounds_ok": firestore.Increment(1 if succeeded else 0),
                    "message_count": firestore.Increment(2),
                    "last_turn_id": rnd.turn_id,
                    "updated_at": now,
                },
            )

    return write
