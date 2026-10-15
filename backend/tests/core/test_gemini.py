"""Gemini helpers with the genai client monkeypatched (no network)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from topskill import gemini
from topskill.errors import ApiError

pytestmark = pytest.mark.unit


class Draft(BaseModel):
    title: str


def api_error(code: int) -> genai_errors.APIError:
    cls = genai_errors.ClientError if code < 500 else genai_errors.ServerError
    return cls(code, {"error": {"code": code, "message": "x", "status": "X"}})


class FakeModels:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    async def generate_content(self, *, model: str, contents: Any, config: types.GenerateContentConfig):
        self.calls.append({"model": model, "config": config})
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch):
    def install(script: list[Any]) -> FakeModels:
        models = FakeModels(script)
        client = SimpleNamespace(aio=SimpleNamespace(models=models))
        monkeypatch.setattr(gemini, "get_genai_client", lambda: client)
        return models

    return install


def structured(parsed: Any = None, text: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(parsed=parsed, text=text)


def test_model_chains_follow_d6() -> None:
    assert gemini.model_chain("chat") == ["gemini-3.8-flash", "gemini-3-flash-preview"]
    assert gemini.model_chain("draft") == ["gemini-3.8-flash", "gemini-3-flash-preview"]
    assert gemini.model_chain("draft_pro") == ["gemini-3.1-pro-preview", "gemini-2.5-pro"]
    assert gemini.model_chain("extract") == ["gemini-3.8-flash", "gemini-3-flash-preview"]
    assert gemini.model_chain("assistant") == ["gemini-3.8-flash", "gemini-3-flash-preview"]
    assert gemini.model_chain("image") == ["gemini-3.1-flash-image", "gemini-2.5-flash-image"]
    assert gemini.MODEL_CHAINS["chat"] == gemini.model_chain("chat")
    assert set(gemini.MODEL_CHAINS) == {"chat", "draft", "draft_pro", "extract", "assistant", "image"}


def test_thinking_config_per_model() -> None:
    cfg = gemini.thinking_config("gemini-3.8-flash", "low")
    assert cfg.thinking_level == types.ThinkingLevel.LOW and cfg.thinking_budget is None
    assert gemini.thinking_config("gemini-3.1-pro-preview", "high").thinking_level == types.ThinkingLevel.HIGH
    cfg = gemini.thinking_config("gemini-2.5-pro", "minimal")
    assert cfg.thinking_budget == 128 and cfg.thinking_level is None
    assert gemini.thinking_config("gemini-2.5-flash", "medium").thinking_budget == 4096
    assert gemini.thinking_config("publishers/google/models/gemini-3-flash-preview", "low").thinking_level


def test_portable_thinking_config_never_sets_level() -> None:
    for level, budget in {"minimal": 128, "low": 1024, "medium": 4096, "high": 8192}.items():
        cfg = gemini.portable_thinking_config(level)  # type: ignore[arg-type]
        assert cfg.thinking_budget == budget and cfg.thinking_level is None
        assert not cfg.include_thoughts


def test_is_retriable() -> None:
    for code in (404, 429, 500, 502, 503, 504):
        assert gemini.is_retriable(api_error(code))
    for code in (400, 401, 403):
        assert not gemini.is_retriable(api_error(code))
    assert not gemini.is_retriable(ValueError("x"))
    wrapped = RuntimeError("wrapped")
    wrapped.__cause__ = api_error(503)
    assert gemini.is_retriable(wrapped)


async def test_generate_structured_first_model(fake_client) -> None:
    models = fake_client([structured(parsed=Draft(title="Visa Advisor"))])
    parsed, model = await gemini.generate_structured(["m-3", "m-2"], "hi", Draft, system_instruction="sys")
    assert parsed == Draft(title="Visa Advisor") and model == "m-3"
    cfg = models.calls[0]["config"]
    assert cfg.response_schema is Draft and cfg.response_mime_type == "application/json"
    assert cfg.system_instruction == "sys" and cfg.temperature is None


async def test_generate_structured_falls_back_on_retriable(fake_client) -> None:
    models = fake_client([api_error(429), structured(text='{"title": "From text"}')])
    parsed, model = await gemini.generate_structured(
        ["gemini-3.8-flash", "gemini-2.5-pro"], "hi", Draft, temperature=0.7
    )
    assert parsed.title == "From text" and model == "gemini-2.5-pro"
    assert models.calls[0]["config"].thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert models.calls[1]["config"].thinking_config.thinking_budget == 1024
    assert models.calls[1]["config"].temperature == 0.7


async def test_generate_structured_unparseable_moves_on(fake_client) -> None:
    fake_client([structured(text="not json"), structured(parsed={"title": "ok"})])
    parsed, model = await gemini.generate_structured(["a", "b"], "hi", Draft)
    assert parsed.title == "ok" and model == "b"


async def test_generate_structured_non_retriable_raises_502(fake_client) -> None:
    models = fake_client([api_error(400), structured(parsed=Draft(title="never"))])
    with pytest.raises(ApiError) as err:
        await gemini.generate_structured(["a", "b"], "hi", Draft)
    assert err.value.status == 502 and err.value.code == "upstream_error"
    assert len(models.calls) == 1


async def test_generate_structured_chain_exhausted(fake_client) -> None:
    fake_client([api_error(503), api_error(404)])
    with pytest.raises(ApiError) as err:
        await gemini.generate_structured(["a", "b"], "hi", Draft)
    assert err.value.code == "upstream_error"


def image_response(data: bytes | None) -> types.GenerateContentResponse:
    parts = [types.Part(text="here")]
    if data is not None:
        parts.append(types.Part(inline_data=types.Blob(data=data, mime_type="image/png")))
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
    )


async def test_generate_image_falls_back(fake_client) -> None:
    models = fake_client([api_error(429), image_response(b"\x89PNG")])
    data, mime = await gemini.generate_image("a fox")
    assert data == b"\x89PNG" and mime == "image/png"
    assert [c["model"] for c in models.calls] == ["gemini-3.1-flash-image", "gemini-2.5-flash-image"]
    assert models.calls[0]["config"].response_modalities == ["IMAGE"]
    assert models.calls[0]["config"].image_config.aspect_ratio == "1:1"


async def test_generate_image_failure(fake_client) -> None:
    fake_client([image_response(None), api_error(500)])
    with pytest.raises(ApiError) as err:
        await gemini.generate_image("a fox")
    assert err.value.status == 502 and err.value.code == "image_failed"
