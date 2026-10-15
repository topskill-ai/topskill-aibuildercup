"""Wire protocol of the voice WebSocket (SPEC §18.1).

Client → server
  * the first frame is a JSON :class:`StartMessage` (Firebase ID token or, in dev/test only, a dev user);
  * then binary frames of raw PCM16 little-endian mono audio at :data:`INPUT_SAMPLE_RATE` Hz;
  * JSON ``{"type": "stop"}`` ends the session gracefully, ``{"type": "ping"}`` keeps a quiet socket alive.

Server → client
  * binary frames of raw PCM16 little-endian mono audio at :data:`OUTPUT_SAMPLE_RATE` Hz;
  * JSON events, each ``{"type": <name>, ...}`` (see the ``*Event`` models below).

Errors are terminal: an ``error`` event (same ``code``/``message``/``trace_id``/``detail`` envelope as
REST, SPEC §6.1) is followed by ``closed`` and a close frame with code ``4000 + HTTP status``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from topskill.domain import Citation, ToolLineDoc
from topskill.events import DoneBilling

INPUT_SAMPLE_RATE = 16_000
OUTPUT_SAMPLE_RATE = 24_000
INPUT_MIME = f"audio/pcm;rate={INPUT_SAMPLE_RATE}"
MAX_AUDIO_FRAME_BYTES = 64 * 1024

# Close codes (RFC 6455 private range 4000–4999): 4000 + the HTTP status of the error.
CLOSE_NORMAL = 1000


def close_code_for(status: int) -> int:
    return 4000 + status if 100 <= status <= 599 else 4500


class DevIdentity(BaseModel):
    uid: str = Field(pattern=r"^[A-Za-z0-9_-]{3,64}$")
    role: Literal["visitor", "creator"] | None = None
    email: str | None = Field(default=None, max_length=200)


class StartMessage(BaseModel):
    type: Literal["start"]
    token: str | None = Field(default=None, max_length=8192)
    dev: DevIdentity | None = None  # honoured only when the dev auth bypass is active (SPEC §4.2)
    conversation_id: str | None = Field(default=None, max_length=64)


ClosedReason = Literal[
    "client",  # the visitor ended the call (stop frame or socket closed)
    "max_duration",  # VOICE_MAX_SESSION_S reached
    "idle",  # nothing said for VOICE_IDLE_TIMEOUT_S
    "replaced",  # the same account opened another voice session
    "error",  # an error event was sent first
    "server",  # the model ended the session (GoAway) or the server is shutting down
]


class ReadyEvent(BaseModel):
    type: Literal["ready"] = "ready"
    session_id: str
    conversation_id: str
    model: str
    input_sample_rate: int = INPUT_SAMPLE_RATE
    output_sample_rate: int = OUTPUT_SAMPLE_RATE
    max_session_s: int
    idle_timeout_s: int
    next_round_charge_cents: int
    balance_cents: int


class TranscriptEvent(BaseModel):
    """A caption delta: append ``text`` to the current ``role`` caption of turn ``turn_id``."""

    type: Literal["transcript"] = "transcript"
    turn_id: str
    role: Literal["user", "model"]
    text: str


class ToolEvent(BaseModel):
    type: Literal["tool"] = "tool"
    turn_id: str
    tool: ToolLineDoc


class InterruptedEvent(BaseModel):
    """Barge-in: the visitor spoke over the answer. Stop playback immediately."""

    type: Literal["interrupted"] = "interrupted"
    turn_id: str


class RoundEvent(BaseModel):
    """One voice round was persisted (and billed when ``outcome == "ok"``)."""

    type: Literal["round"] = "round"
    turn_id: str
    conversation_id: str
    message_id: str
    outcome: Literal["ok", "failed", "aborted"]
    citations: list[Citation] = []
    billing: DoneBilling | None = None


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str
    trace_id: str
    detail: dict | None = None


class ClosedEvent(BaseModel):
    type: Literal["closed"] = "closed"
    reason: ClosedReason
    conversation_id: str | None = None
    rounds: int = 0
    duration_s: int = 0
