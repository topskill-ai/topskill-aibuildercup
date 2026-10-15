"""Gemini Live on Vertex AI (SPEC §18.1).

* :class:`LiveConnector` is the seam between the voice session and Google: the real
  :class:`GenaiConnector` wraps ``client.aio.live.connect`` (one Vertex client per location, ADC); tests
  install a scripted fake with :func:`set_connector`.
* :func:`live_plans` = ``[(VOICE_MODEL, VOICE_LOCATION), (VOICE_MODEL_FALLBACK, VOICE_LOCATION_FALLBACK)]``;
  the session connects with the first plan that answers the setup handshake.
* :func:`build_live_config` mirrors the text chat: same instruction, function tools (knowledge search,
  skill readers), built-in Google Search when the agent has web search on, audio in/out transcription and
  server-side voice activity detection with barge-in (the visitor's speech interrupts the answer).
* :class:`ToolBridge` runs ADK ``FunctionTool``s (the same factories the text chat uses) for Live
  ``tool_call`` messages.

Live findings (Vertex, project top-skill, google-genai 2.31.0, 2026-10-15):
* ``gemini-live-2.5-flash`` answers on location ``global``; ``gemini-live-2.5-flash-native-audio`` only on
  ``us-central1``. Every newer name tried (``gemini-3.1-flash-live-preview``, ``gemini-live-3*``, …) is
  "publisher model not found" in every location.
* Both accept a function tool and ``google_search`` in one session; grounding metadata arrives in
  ``server_content.grounding_metadata`` (web chunks + search entry point).
* A model turn that only calls a tool ends with its own ``turn_complete`` (no audio); the spoken answer
  follows the tool response as another turn. ``session.receive()`` stops after every ``turn_complete``,
  so :meth:`GenaiSession.messages` re-enters it until the socket closes.
* Output audio is ``audio/pcm`` at 24 kHz; usage metadata is per model turn (not cumulative).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from topskill.config import get_settings
from topskill.voice.protocol import INPUT_MIME

log = logging.getLogger(__name__)

TOOL_TIMEOUT_S = 30.0


class LiveSession(Protocol):
    async def send_audio(self, pcm: bytes) -> None: ...

    async def send_tool_responses(self, responses: list[types.FunctionResponse]) -> None: ...

    def messages(self) -> AsyncIterator[types.LiveServerMessage]: ...


class LiveConnector(Protocol):
    def connect(
        self, *, model: str, location: str, config: types.LiveConnectConfig
    ) -> AbstractAsyncContextManager[LiveSession]: ...


# --------------------------------------------------------------------------- real connector


class GenaiSession:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def send_audio(self, pcm: bytes) -> None:
        await self._session.send_realtime_input(audio=types.Blob(data=pcm, mime_type=INPUT_MIME))

    async def send_tool_responses(self, responses: list[types.FunctionResponse]) -> None:
        await self._session.send_tool_response(function_responses=responses)

    async def messages(self) -> AsyncIterator[types.LiveServerMessage]:
        while True:
            try:
                async for msg in self._session.receive():
                    yield msg
            except genai_errors.APIError as exc:
                if exc.code == 1000:  # normal closure by the server
                    return
                raise


class GenaiConnector:
    """Vertex AI Live (ADC). One ``genai.Client`` per location, created lazily."""

    def __init__(self) -> None:
        self._clients: dict[str, genai.Client] = {}
        self._lock = threading.Lock()

    def _client(self, location: str) -> genai.Client:
        with self._lock:
            client = self._clients.get(location)
            if client is None:
                client = genai.Client(
                    vertexai=True, project=get_settings().google_cloud_project, location=location
                )
                self._clients[location] = client
            return client

    @asynccontextmanager
    async def connect(
        self, *, model: str, location: str, config: types.LiveConnectConfig
    ) -> AsyncIterator[LiveSession]:
        async with self._client(location).aio.live.connect(model=model, config=config) as session:
            yield GenaiSession(session)


_connector: LiveConnector | None = None


def get_connector() -> LiveConnector:
    global _connector
    if _connector is None:
        _connector = GenaiConnector()
    return _connector


def set_connector(connector: LiveConnector | None) -> None:
    """Tests only: install a fake connector (``None`` restores the real one)."""
    global _connector
    _connector = connector


def live_plans() -> list[tuple[str, str]]:
    """``[(model, location), …]`` without duplicates, primary first."""
    s = get_settings()
    plans: list[tuple[str, str]] = []
    for plan in ((s.voice_model, s.voice_location), (s.voice_model_fallback, s.voice_location_fallback)):
        if plan[0] and plan[1] and plan not in plans:
            plans.append(plan)
    return plans


# --------------------------------------------------------------------------- config


def build_live_config(
    *,
    instruction: str,
    declarations: list[types.FunctionDeclaration],
    web_search: bool,
    temperature: float | None,
) -> types.LiveConnectConfig:
    tools: list[types.Tool] = []
    if declarations:
        tools.append(types.Tool(function_declarations=declarations))
    if web_search:
        tools.append(types.Tool(google_search=types.GoogleSearch()))
    return types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=types.Content(role="system", parts=[types.Part(text=instruction)]),
        tools=tools or None,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=False),
            activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
        ),
        **({"temperature": temperature} if temperature is not None else {}),
    )


# --------------------------------------------------------------------------- tools

ToolFactory = Callable[[Any], list[Any]]  # (citation collector) -> ADK FunctionTools


class ToolBridge:
    """Runs the text chat's ADK ``FunctionTool``s for Live ``tool_call`` messages.

    ``factory(collector)`` returns the round's tools (the knowledge search tool is bound to the round's
    citation collector, so ``[n]`` numbers stay stable within a round). Declarations are taken once.
    """

    def __init__(self, factory: ToolFactory, probe_collector: Any) -> None:
        self._factory = factory
        self.declarations: list[types.FunctionDeclaration] = []
        for tool in factory(probe_collector):
            decl = tool._get_declaration()  # ADK FunctionTool → genai FunctionDeclaration
            if decl is not None:
                self.declarations.append(decl)

    def tools_for(self, collector: Any) -> dict[str, Any]:
        return {tool.name: tool for tool in self._factory(collector)}

    @staticmethod
    async def call(tools: dict[str, Any], name: str, args: dict[str, Any] | None) -> dict:
        """Run one tool; never raises (the model gets an ``error`` field instead)."""
        tool = tools.get(name)
        if tool is None:
            return {"error": "Unknown tool."}
        func = tool.func
        params = inspect.signature(func).parameters
        kwargs = {k: v for k, v in (args or {}).items() if k in params}
        try:
            result = func(**kwargs)
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, timeout=TOOL_TIMEOUT_S)
        except Exception:
            log.warning("voice_tool_failed tool=%s", name, exc_info=True)
            return {"error": "The tool failed. Answer without it and say so."}
        return result if isinstance(result, dict) else {"result": result}
