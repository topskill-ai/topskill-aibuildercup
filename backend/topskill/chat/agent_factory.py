"""Builds ADK ``LlmAgent``s from Firestore config (SPEC §10.4 "Agent factory").

* Web channel: one agent per fallback plan for one known model → ``thinking_config(model, "low")``
  (``thinking_level`` on gemini-3*). Built by ``chat.runtime`` with :func:`build_llm_agent`.
* A2A channel (:func:`build_runtime_agent`): one ``FallbackModel`` that may reach another model →
  ``portable_thinking_config("low")`` (``thinking_budget``), never ``thinking_level``; no ``save_record``;
  ``search_knowledge`` without a collector (the stack is shared by concurrent calls).

Search modes: ``native`` adds the built-in ``google_search`` tool; ``bypass`` uses
``GoogleSearchTool(bypass_multi_tools_limit=True)``, which ADK turns into the ``google_search_agent``
function tool when other tools exist (needed on gemini-2.5*); ``off`` adds nothing.
Native search mixed with function tools was verified live on gemini-3.8-flash (see ``chat.runtime``),
so bypass mode is only chosen when a model that may serve the request is not gemini-3*.
"""

from __future__ import annotations

from typing import Literal

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import BaseLlm, FallbackModel, Gemini
from google.adk.tools import google_search
from google.adk.tools.google_search_tool import GoogleSearchTool
from google.genai import types

from topskill.chat.deps import get_deps
from topskill.chat.prompt import build_instruction
from topskill.chat.skill_tools import make_skill_tools, skills_fit_inline
from topskill.config import get_settings
from topskill.domain import AgentDoc, AgentVersion
from topskill.gemini import RETRIABLE_STATUS_CODES, portable_thinking_config

SearchMode = Literal["native", "bypass", "off"]

THINKING_LEVEL = "low"
RETRY_OPTIONS = types.HttpRetryOptions(initial_delay=1, attempts=2)


def agent_name(slug: str) -> str:
    """``"agent_" + slug`` as a valid identifier (≤ 56 chars)."""
    return "agent_" + slug.replace("-", "_")[:50]


def model_id(model: str | BaseLlm) -> str:
    name = model if isinstance(model, str) else model.model
    return name.rsplit("/", 1)[-1].lower()


def supports_native_mixing(model: str | BaseLlm) -> bool:
    """Built-in google_search together with function tools works on gemini-3* (verified live)."""
    return model_id(model).startswith("gemini-3")


def search_mode_for(models: list[str], *, web_enabled: bool, has_function_tools: bool) -> SearchMode:
    """``off`` without web search; ``native`` when every model that may serve the request mixes tools
    natively (or there is no function tool); else ``bypass``."""
    if not web_enabled:
        return "off"
    if not has_function_tools or all(supports_native_mixing(m) for m in models):
        return "native"
    return "bypass"


def live_model(name: str) -> Gemini:
    return Gemini(model=name, retry_options=RETRY_OPTIONS)


def _static_instruction(text: str):
    # An InstructionProvider bypasses ADK's {state} placeholder injection: skill bodies may contain braces.
    def provider(_ctx: ReadonlyContext) -> str:
        return text

    return provider


def build_llm_agent(
    agent: AgentDoc,
    version: AgentVersion,
    *,
    model: str | BaseLlm,
    instruction: str,
    tools: list,
    search_mode: SearchMode,
    temperature: float | None,
    thinking: types.ThinkingConfig,
) -> LlmAgent:
    all_tools = list(tools)
    if search_mode == "native":
        all_tools.append(google_search)
    elif search_mode == "bypass":
        all_tools.append(GoogleSearchTool(bypass_multi_tools_limit=True))
    config = types.GenerateContentConfig(
        thinking_config=thinking, **({"temperature": temperature} if temperature is not None else {})
    )
    return LlmAgent(
        name=agent_name(agent.slug),
        description=(agent.tagline or version.title)[:500],
        model=model,
        instruction=_static_instruction(instruction),
        tools=all_tools,
        generate_content_config=config,
    )


def chat_models(version: AgentVersion) -> list[str]:
    """``[version.model, MODEL_CHAT_FALLBACK]`` without duplicates (the web plans / A2A chain)."""
    models = [version.model]
    fallback = get_settings().model_chat_fallback
    if fallback and fallback not in models:
        models.append(fallback)
    return models


async def build_runtime_agent(agent: AgentDoc, version: AgentVersion, *, channel: Literal["a2a"]) -> LlmAgent:
    """The A2A agent: FallbackModel + portable thinking, knowledge without a collector, no records."""
    if channel != "a2a":
        raise ValueError("build_runtime_agent only builds the a2a channel")
    deps = get_deps()
    has_knowledge = await deps.knowledge.has_ready_files(agent.id)
    tools: list = []
    if has_knowledge:
        tools.append(deps.knowledge.make_search_tool(agent.id, None))
    tools.extend(make_skill_tools(version.skills, inline=skills_fit_inline(version.skills)))
    instruction = await build_instruction(
        agent, version, channel="a2a", has_knowledge=has_knowledge, record_section=None
    )
    models = chat_models(version)
    model: BaseLlm
    if get_settings().llm_mode == "fake":
        from topskill.testing import fake_llm

        model = fake_llm.make_fake_model(models[0])
    else:
        model = FallbackModel(
            models=[live_model(models[0]), *models[1:]],
            retriable_status_codes=RETRIABLE_STATUS_CODES,
        )
    return build_llm_agent(
        agent,
        version,
        model=model,
        instruction=instruction,
        tools=tools,
        search_mode=search_mode_for(
            models, web_enabled=version.web_search_enabled, has_function_tools=bool(tools)
        ),
        temperature=version.temperature,
        thinking=portable_thinking_config(THINKING_LEVEL),
    )
