"""Gemini helpers on Vertex AI via ADC (SPEC §4.4, D6).

* ``model_chain(name)`` – ordered models for a use case; a chain moves to the next model on 429/5xx/404
  and only before any output was produced.
* ``thinking_config(model, level)`` – for exactly one known model (``thinking_level`` on gemini-3*).
* ``portable_thinking_config(level)`` – ``thinking_budget`` only; MUST be used whenever one config can reach
  more than one model (``FallbackModel``, mixed 3.x/2.5 chains).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator, Mapping
from typing import Literal

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError

from topskill.config import get_settings
from topskill.errors import ApiError, Codes

log = logging.getLogger(__name__)

ThinkingLevel = Literal["minimal", "low", "medium", "high"]
ChainName = Literal["chat", "draft", "draft_pro", "extract", "assistant", "image"]
CHAIN_NAMES: tuple[ChainName, ...] = ("chat", "draft", "draft_pro", "extract", "assistant", "image")

THINKING_BUDGETS: dict[str, int] = {"minimal": 128, "low": 1024, "medium": 4096, "high": 8192}
_THINKING_LEVELS: dict[str, types.ThinkingLevel] = {
    "minimal": types.ThinkingLevel.MINIMAL,
    "low": types.ThinkingLevel.LOW,
    "medium": types.ThinkingLevel.MEDIUM,
    "high": types.ThinkingLevel.HIGH,
}
RETRIABLE_STATUS_CODES: frozenset[int] = frozenset({404, 429, 500, 502, 503, 504})

_client: genai.Client | None = None
_client_lock = threading.Lock()


def get_genai_client() -> genai.Client:
    """Vertex AI client (ADC, ``GOOGLE_CLOUD_PROJECT`` / ``GOOGLE_CLOUD_LOCATION``), module singleton.
    No API keys: the Developer-API client for File Search lives in the knowledge package."""
    global _client
    with _client_lock:
        if _client is None:
            settings = get_settings()
            _client = genai.Client(
                vertexai=True,
                project=settings.google_cloud_project,
                location=settings.google_cloud_location,
            )
        return _client


def model_chain(name: ChainName) -> list[str]:
    """``[primary, fallback]`` from settings (duplicates removed, order kept)."""
    s = get_settings()
    pairs: dict[str, tuple[str, str]] = {
        "chat": (s.model_chat, s.model_chat_fallback),
        "draft": (s.model_draft, s.model_draft_fallback),
        "draft_pro": (s.model_draft_pro, s.model_draft_pro_fallback),
        "extract": (s.model_extract, s.model_extract_fallback),
        "assistant": (s.model_assistant, s.model_assistant_fallback),
        "image": (s.model_image, s.model_image_fallback),
    }
    if name not in pairs:
        raise KeyError(name)
    chain: list[str] = []
    for model in pairs[name]:
        if model and model not in chain:
            chain.append(model)
    return chain


class _ModelChains(Mapping[str, list[str]]):
    """``MODEL_CHAINS[name]`` view over the current settings (SPEC §4.4)."""

    def __getitem__(self, key: str) -> list[str]:
        if key not in CHAIN_NAMES:
            raise KeyError(key)
        return model_chain(key)  # type: ignore[arg-type]

    def __iter__(self) -> Iterator[str]:
        return iter(CHAIN_NAMES)

    def __len__(self) -> int:
        return len(CHAIN_NAMES)


MODEL_CHAINS: Mapping[str, list[str]] = _ModelChains()


def _model_id(model: str) -> str:
    return model.rsplit("/", 1)[-1].lower()


def thinking_config(model: str, level: ThinkingLevel) -> types.ThinkingConfig:
    """Thinking config for one known model: ``thinking_level`` on gemini-3*, ``thinking_budget`` otherwise."""
    if level not in THINKING_BUDGETS:
        raise ValueError(f"unknown thinking level: {level}")
    if _model_id(model).startswith("gemini-3"):
        return types.ThinkingConfig(thinking_level=_THINKING_LEVELS[level])
    return types.ThinkingConfig(thinking_budget=THINKING_BUDGETS[level])


def portable_thinking_config(level: ThinkingLevel) -> types.ThinkingConfig:
    """``thinking_budget`` only – accepted by gemini-3* and gemini-2.5* alike (2.5 rejects thinking_level)."""
    if level not in THINKING_BUDGETS:
        raise ValueError(f"unknown thinking level: {level}")
    return types.ThinkingConfig(thinking_budget=THINKING_BUDGETS[level])


def error_status(exc: BaseException) -> int | None:
    """HTTP-ish status of a genai/ADK/HTTP error, if any."""
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def is_retriable(exc: BaseException) -> bool:
    """True for 429/500/502/503/504 and 404 (model not found) from genai/ADK errors."""
    if isinstance(exc, genai_errors.APIError):
        return exc.code in RETRIABLE_STATUS_CODES
    status = error_status(exc)
    if status is not None:
        return status in RETRIABLE_STATUS_CODES
    cause = exc.__cause__
    return cause is not None and cause is not exc and is_retriable(cause)


def _parse[T: BaseModel](response: types.GenerateContentResponse, schema: type[T]) -> T:
    parsed = response.parsed
    if isinstance(parsed, schema):
        return parsed
    if isinstance(parsed, dict):
        return schema.model_validate(parsed)
    text = response.text
    if not text:
        raise ValueError("empty structured response")
    return schema.model_validate_json(text)


async def generate_structured[T: BaseModel](
    chain: list[str],
    contents: str | list,
    schema: type[T],
    *,
    system_instruction: str | None = None,
    thinking: ThinkingLevel | None = "low",
    temperature: float | None = None,
) -> tuple[T, str]:
    """Structured output (``response_schema=schema``) tried along ``chain``; returns ``(parsed, model_used)``.

    Moves to the next model on retriable errors and on unparseable output; any other error, or the end of
    the chain, raises ``ApiError(502, "upstream_error")``. Callers map it to their own code if needed.
    """
    if not chain:
        raise ValueError("empty model chain")
    client = get_genai_client()
    last_error: BaseException | None = None
    for model in chain:
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            system_instruction=system_instruction,
            thinking_config=thinking_config(model, thinking) if thinking else None,
            **({"temperature": temperature} if temperature is not None else {}),
        )
        try:
            response = await client.aio.models.generate_content(model=model, contents=contents, config=config)
        except Exception as exc:
            last_error = exc
            if is_retriable(exc):
                log.warning("structured_retry model=%s status=%s", model, error_status(exc))
                continue
            log.error("structured_failed model=%s error=%s", model, str(exc)[:500])
            raise ApiError(502, Codes.UPSTREAM_ERROR, "The model request failed. Please try again.") from exc
        try:
            return _parse(response, schema), model
        except (ValidationError, ValueError) as exc:
            last_error = exc
            log.warning("structured_unparseable model=%s error=%s", model, str(exc)[:300])
            continue
    log.error("structured_chain_exhausted chain=%s error=%s", chain, str(last_error)[:500])
    raise ApiError(502, Codes.UPSTREAM_ERROR, "The model is busy. Please try again.") from last_error


async def generate_image(prompt: str) -> tuple[bytes, str]:
    """Square image from the ``image`` chain → ``(bytes, mime)``; ``ApiError(502, "image_failed")``."""
    client = get_genai_client()
    config = types.GenerateContentConfig(
        response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio="1:1")
    )
    last_error: BaseException | None = None
    for model in model_chain("image"):
        try:
            response = await client.aio.models.generate_content(model=model, contents=prompt, config=config)
        except Exception as exc:
            last_error = exc
            log.warning("image_attempt_failed model=%s status=%s", model, error_status(exc))
            continue
        for candidate in response.candidates or []:
            for part in (candidate.content.parts if candidate.content else None) or []:
                blob = part.inline_data
                if blob is not None and blob.data:
                    return blob.data, blob.mime_type or "image/png"
        last_error = RuntimeError("no image in response")
        log.warning("image_missing model=%s", model)
    log.error("image_chain_exhausted error=%s", str(last_error)[:500])
    raise ApiError(502, Codes.IMAGE_FAILED, "Image generation failed. Please try again.") from last_error
