"""Test doubles for the voice lane: an in-process ASGI WebSocket client and a scripted Gemini Live fake.

``AsgiWebSocket`` drives the FastAPI app on the test's own event loop (the module-level Firestore
client must never cross loops, so Starlette's threaded TestClient is not used).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from google.genai import types

from tests.chat.doubles import FakeKnowledge
from topskill.domain import Passage
from topskill.knowledge.citations import Collector

TIMEOUT = 10.0


class Closed(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"closed {code}")
        self.code = code


class AsgiWebSocket:
    def __init__(self, app: Any, path: str, headers: dict[str, str] | None = None) -> None:
        self.app = app
        self.path = path
        self.headers = headers or {}
        self._to_app: asyncio.Queue[dict] = asyncio.Queue()
        self._from_app: asyncio.Queue[dict] = asyncio.Queue()
        self.task: asyncio.Task | None = None
        self.accepted = False
        self.close_code: int | None = None

    async def _receive(self) -> dict:
        return await self._to_app.get()

    async def _send(self, message: dict) -> None:
        await self._from_app.put(message)

    async def connect(self) -> AsgiWebSocket:
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "http_version": "1.1",
            "path": self.path,
            "raw_path": self.path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in self.headers.items()],
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
            "subprotocols": [],
            "state": {},
        }
        self.task = asyncio.create_task(self.app(scope, self._receive, self._send))
        await self._to_app.put({"type": "websocket.connect"})
        first = await asyncio.wait_for(self._from_app.get(), TIMEOUT)
        if first["type"] == "websocket.accept":
            self.accepted = True
        else:
            self.close_code = first.get("code", 1000)
        return self

    async def send_json(self, payload: dict) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": json.dumps(payload)})

    async def send_text(self, text: str) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": text})

    async def send_bytes(self, data: bytes) -> None:
        await self._to_app.put({"type": "websocket.receive", "bytes": data})

    async def receive(self, timeout: float = TIMEOUT) -> dict | bytes:  # noqa: ASYNC109
        """Next JSON event (dict) or audio frame (bytes); raises :class:`Closed` on a close frame."""
        message = await asyncio.wait_for(self._from_app.get(), timeout)
        if message["type"] == "websocket.close":
            self.close_code = message.get("code", 1000)
            raise Closed(self.close_code)
        if message.get("bytes") is not None:
            return message["bytes"]
        return json.loads(message["text"])

    async def next_event(self, kind: str, timeout: float = TIMEOUT) -> dict:  # noqa: ASYNC109
        """Skip audio frames and other events until an event of type ``kind``."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            left = deadline - asyncio.get_running_loop().time()
            item = await self.receive(max(0.01, left))
            if isinstance(item, dict) and item.get("type") == kind:
                return item

    async def drain(self, timeout: float = TIMEOUT) -> list[dict | bytes]:  # noqa: ASYNC109
        """Everything until the close frame."""
        items: list[dict | bytes] = []
        try:
            while True:
                items.append(await self.receive(timeout))
        except Closed:
            return items

    async def disconnect(self) -> None:
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        await self.finish()

    async def finish(self) -> None:
        if self.task is not None:
            await asyncio.wait_for(self.task, TIMEOUT)


# --------------------------------------------------------------------------- Gemini Live fake


class FakeLiveSession:
    def __init__(self, model: str, config: types.LiveConnectConfig) -> None:
        self.model = model
        self.config = config
        self.audio = bytearray()
        self.audio_frames = 0
        self.tool_responses: list[list[types.FunctionResponse]] = []
        self.inbox: asyncio.Queue[Any] = asyncio.Queue()
        self.got_audio = asyncio.Event()
        self.got_tool_response = asyncio.Event()
        self.closed = False

    async def send_audio(self, pcm: bytes) -> None:
        self.audio += pcm
        self.audio_frames += 1
        self.got_audio.set()

    async def send_tool_responses(self, responses: list[types.FunctionResponse]) -> None:
        self.tool_responses.append(responses)
        self.got_tool_response.set()

    async def messages(self) -> AsyncIterator[types.LiveServerMessage]:
        while True:
            item = await self.inbox.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

    def push(self, *messages: Any) -> None:
        for m in messages:
            self.inbox.put_nowait(m)


class FakeConnector:
    def __init__(self, fail_models: tuple[str, ...] = (), hang_models: tuple[str, ...] = ()) -> None:
        self.fail_models = fail_models
        self.hang_models = hang_models  # accept the socket but never answer the setup
        self.hung_cancelled = 0
        self.attempts: list[tuple[str, str]] = []
        self.sessions: list[FakeLiveSession] = []
        self._new_session = asyncio.Event()

    @asynccontextmanager
    async def connect(self, *, model: str, location: str, config: types.LiveConnectConfig):
        self.attempts.append((model, location))
        if model in self.fail_models:
            raise RuntimeError(f"1008 Publisher model {model} was not found")
        if model in self.hang_models:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.hung_cancelled += 1
                raise
        session = FakeLiveSession(model, config)
        self.sessions.append(session)
        self._new_session.set()
        try:
            yield session
        finally:
            session.closed = True

    async def session(self, index: int = -1) -> FakeLiveSession:
        while not self.sessions:
            self._new_session.clear()
            await asyncio.wait_for(self._new_session.wait(), TIMEOUT)
        return self.sessions[index]


# ---- message builders


def msg(**content: Any) -> types.LiveServerMessage:
    return types.LiveServerMessage(server_content=types.LiveServerContent(**content))


def heard(text: str) -> types.LiveServerMessage:
    return msg(input_transcription=types.Transcription(text=text))


def said(text: str) -> types.LiveServerMessage:
    return msg(output_transcription=types.Transcription(text=text))


def audio(n: int = 4800) -> types.LiveServerMessage:
    blob = types.Blob(data=b"\x01\x00" * (n // 2), mime_type="audio/pcm")
    return msg(model_turn=types.Content(role="model", parts=[types.Part(inline_data=blob)]))


def turn_complete() -> types.LiveServerMessage:
    return msg(turn_complete=True)


def interrupted() -> types.LiveServerMessage:
    return msg(interrupted=True)


def go_away(time_left: str = "5s") -> types.LiveServerMessage:
    return types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left=time_left))


def usage(prompt: int, response: int) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        usage_metadata=types.UsageMetadata(prompt_token_count=prompt, response_token_count=response)
    )


def tool_call(name: str, args: dict, call_id: str = "call-1") -> types.LiveServerMessage:
    return types.LiveServerMessage(
        tool_call=types.LiveServerToolCall(
            function_calls=[types.FunctionCall(id=call_id, name=name, args=args)]
        )
    )


def web_grounding(query: str, uri: str, title: str) -> types.LiveServerMessage:
    return msg(
        grounding_metadata=types.GroundingMetadata(
            web_search_queries=[query],
            grounding_chunks=[types.GroundingChunk(web=types.GroundingChunkWeb(uri=uri, title=title))],
        )
    )


def answer(text: str, *, audio_bytes: int = 4800, prompt: int = 100, response: int = 50) -> list:
    """A complete spoken answer: audio + caption + usage + turn_complete."""
    return [audio(audio_bytes), said(text), usage(prompt, response), turn_complete()]


class VoiceKnowledge(FakeKnowledge):
    """FakeKnowledge with the real citation collector (so stored citations are real)."""

    def new_collector(self) -> Collector:
        return Collector()


def visa_passage() -> Passage:
    return Passage(
        file_id="kf_1",
        filename="visa_guide.pdf",
        text="The work visa fee is 290 USD.",
        page_start=2,
        page_end=2,
        score=0.1,
    )
