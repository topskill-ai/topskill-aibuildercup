"""Scripted ADK ``BaseLlm`` for tests (SPEC §10.4, §12.1). Selected by ``LLM_MODE=fake``.

A script is a flat list of :class:`FakeStep` built with the helpers below. Every model call
(``generate_content_async``) consumes steps in order until one response is complete:

* ``text(chunks)``      – streamed as one partial response per chunk; aggregated into the final response.
* ``thought(text)``     – a thought part (must never reach the visitor).
* ``call(name, args)``  – a function call. A response ends after a call, because ADK then runs the tool
                          and calls the model again (next steps); ``call(..., parallel=True)`` joins the
                          previous call's response (parallel function calls).
* ``grounding(...)``    – Google Search grounding metadata attached to the response.
* ``usage(...)``        – token usage on the final response (a default is always attached).
* ``pause(seconds)``    – sleep inside the response (lets tests abort/disconnect mid-stream).
* ``error(status)``     – raise a google-genai ``APIError`` with that HTTP status (429/5xx/404 are
                          retriable). Ends the response.
* ``error_event(code)`` – yield an ``LlmResponse`` with ``error_code`` set (e.g. ``"SAFETY"``). Ends it.
* ``empty()``           – a final response without any text. Ends it.
* ``end()``             – ends the current response (e.g. between a search sub-agent's answer and the
                          main agent's answer, which share one fake model in bypass mode).

When the script is exhausted, further model calls return an empty response.

``LLM_MODE=fake`` code paths call :func:`make_fake_model`, which pops the next queued script (see
:func:`queue_script`) or uses :data:`DEFAULT_ANSWER`. Each fallback plan / runtime agent build pops its
own script, so ``queue_script([error(429)]); queue_script([text("ok")])`` exercises a model fallback.
"""

from __future__ import annotations

import asyncio
import collections
from collections.abc import AsyncGenerator, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from google.adk.models import BaseLlm, LlmCapabilities, LlmRequest, LlmResponse
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import Field, PrivateAttr

StepKind = Literal[
    "text", "thought", "call", "grounding", "usage", "pause", "error", "error_event", "empty", "end"
]

DEFAULT_ANSWER = "This is a scripted answer from the fake model."
DEFAULT_MODEL = "gemini-3.8-flash"


@dataclass(frozen=True)
class FakeStep:
    kind: StepKind
    chunks: tuple[str, ...] = ()
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None
    parallel: bool = False
    grounding: types.GroundingMetadata | None = None
    status: int = 0
    code: str = ""
    message: str = ""
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


def text(chunks: str | Sequence[str]) -> FakeStep:
    return FakeStep("text", chunks=(chunks,) if isinstance(chunks, str) else tuple(chunks))


def thought(value: str) -> FakeStep:
    return FakeStep("thought", chunks=(value,))


def call(
    name: str, args: dict[str, Any] | None = None, *, call_id: str | None = None, parallel: bool = False
) -> FakeStep:
    return FakeStep("call", name=name, args=dict(args or {}), call_id=call_id, parallel=parallel)


def grounding(
    web: Iterable[tuple[str, str] | dict[str, str]] = (),
    queries: Iterable[str] = (),
    *,
    entry_point_html: str | None = None,
) -> FakeStep:
    """``web`` items are ``(title, uri)`` or ``{"title", "uri", "domain"}``."""
    chunks = []
    for item in web:
        if isinstance(item, tuple):
            title, uri = item
            chunks.append(
                types.GroundingChunk(web=types.GroundingChunkWeb(title=title, uri=uri, domain=title))
            )
        else:
            chunks.append(types.GroundingChunk(web=types.GroundingChunkWeb(**item)))
    gm = types.GroundingMetadata(
        grounding_chunks=chunks or None,
        web_search_queries=list(queries) or None,
        search_entry_point=(
            types.SearchEntryPoint(rendered_content=entry_point_html) if entry_point_html else None
        ),
    )
    return FakeStep("grounding", grounding=gm)


def usage(input_tokens: int, output_tokens: int) -> FakeStep:
    return FakeStep("usage", input_tokens=input_tokens, output_tokens=output_tokens)


def pause(seconds: float) -> FakeStep:
    return FakeStep("pause", seconds=seconds)


def error(status: int, message: str = "fake upstream error") -> FakeStep:
    return FakeStep("error", status=status, message=message)


def error_event(code: str, message: str = "") -> FakeStep:
    return FakeStep("error_event", code=code, message=message or f"fake {code}")


def empty() -> FakeStep:
    return FakeStep("empty")


def end() -> FakeStep:
    return FakeStep("end")


def api_error(status: int, message: str = "fake upstream error") -> genai_errors.APIError:
    body = {"error": {"code": status, "message": message, "status": "FAKE"}}
    if 400 <= status < 500:
        return genai_errors.ClientError(status, body)
    return genai_errors.ServerError(status, body)


class FakeLlm(BaseLlm):
    """Scripted model. ``requests`` records every ``LlmRequest`` it received."""

    model: str = DEFAULT_MODEL
    script: list[FakeStep] = Field(default_factory=list)
    _cursor: int = PrivateAttr(default=0)
    _calls: int = PrivateAttr(default=0)
    _requests: list[LlmRequest] = PrivateAttr(default_factory=list)

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=True)

    @property
    def requests(self) -> list[LlmRequest]:
        return self._requests

    @property
    def exhausted(self) -> bool:
        return self._cursor >= len(self.script)

    def _take_response_steps(self) -> list[FakeStep]:
        steps: list[FakeStep] = []
        while self._cursor < len(self.script):
            step = self.script[self._cursor]
            self._cursor += 1
            steps.append(step)
            if step.kind in ("error", "error_event", "empty", "end"):
                break
            if step.kind == "call":
                nxt = self.script[self._cursor] if self._cursor < len(self.script) else None
                if nxt is None or nxt.kind != "call" or not nxt.parallel:
                    break
        return steps

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._requests.append(llm_request)
        steps = self._take_response_steps()
        text_parts: list[str] = []
        thought_parts: list[str] = []
        calls: list[types.FunctionCall] = []
        gm: types.GroundingMetadata | None = None
        in_tokens, out_tokens = 10, 0
        for step in steps:
            if step.kind == "error":
                raise api_error(step.status, step.message)
            if step.kind == "error_event":
                yield LlmResponse(
                    error_code=step.code,
                    error_message=step.message,
                    finish_reason=types.FinishReason.SAFETY if step.code == "SAFETY" else None,
                    model_version=self.model,
                )
                return
            if step.kind == "pause":
                await asyncio.sleep(step.seconds)
            elif step.kind == "text":
                for chunk in step.chunks:
                    text_parts.append(chunk)
                    out_tokens += max(1, len(chunk) // 4)
                    if stream:
                        yield LlmResponse(
                            content=types.Content(role="model", parts=[types.Part(text=chunk)]),
                            partial=True,
                            model_version=self.model,
                        )
            elif step.kind == "thought":
                thought_parts.extend(step.chunks)
                if stream:
                    yield LlmResponse(
                        content=types.Content(
                            role="model", parts=[types.Part(text=step.chunks[0], thought=True)]
                        ),
                        partial=True,
                        model_version=self.model,
                    )
            elif step.kind == "call":
                self._calls += 1
                calls.append(
                    types.FunctionCall(
                        id=step.call_id or f"fake-call-{self._calls}", name=step.name, args=step.args
                    )
                )
            elif step.kind == "grounding":
                gm = step.grounding
            elif step.kind == "usage":
                in_tokens, out_tokens = step.input_tokens, step.output_tokens
        parts: list[types.Part] = []
        if thought_parts:
            parts.append(types.Part(text="".join(thought_parts), thought=True))
        if text_parts:
            parts.append(types.Part(text="".join(text_parts)))
        parts.extend(types.Part(function_call=fc) for fc in calls)
        if not parts:
            parts.append(types.Part(text=""))
        yield LlmResponse(
            content=types.Content(role="model", parts=parts),
            partial=False,
            grounding_metadata=gm,
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=in_tokens,
                candidates_token_count=out_tokens,
                total_token_count=in_tokens + out_tokens,
            ),
            finish_reason=types.FinishReason.STOP,
            model_version=self.model,
        )


# --------------------------------------------------------------------------- registry used by LLM_MODE=fake

_scripts: collections.deque[list[FakeStep]] = collections.deque()
created: list[FakeLlm] = []


def queue_script(steps: Sequence[FakeStep]) -> None:
    """Queue the script for the next fake model built by ``LLM_MODE=fake`` code."""
    _scripts.append(list(steps))


def reset() -> None:
    """Forget queued scripts and created models (call at the start of each test)."""
    _scripts.clear()
    created.clear()


def pending_scripts() -> int:
    return len(_scripts)


def make_fake_model(model: str = DEFAULT_MODEL) -> FakeLlm:
    """The next queued script as a ``FakeLlm`` named ``model`` (default script: one short answer)."""
    script = _scripts.popleft() if _scripts else [text(DEFAULT_ANSWER)]
    llm = FakeLlm(model=model, script=script)
    created.append(llm)
    return llm
