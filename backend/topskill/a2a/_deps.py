"""The a2a lane's only doors into other domain packages (SPEC §3.1).

billing/knowledge are reached through ``topskill.chat.get_deps()`` and the runtime agent through
``topskill.chat.build_runtime_agent``. Callers use ``_deps.get_deps()`` / ``_deps.build_runtime_agent()``
through the module attribute so tests can substitute them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from google.adk.agents import LlmAgent

    from topskill.domain import AgentDoc, AgentVersion


def get_deps() -> Any:
    """``topskill.chat.get_deps()`` (billing, knowledge, records; real packages or stubs)."""
    from topskill.chat import get_deps as chat_get_deps

    return chat_get_deps()


def billing() -> Any:
    return get_deps().billing


async def build_runtime_agent(agent: AgentDoc, version: AgentVersion) -> LlmAgent:
    """The A2A-channel ADK agent: ``FallbackModel`` + ``portable_thinking_config("low")``, records off,
    ``search_knowledge`` with ``collector=None`` (SPEC §9.1 step 1, §10.4)."""
    from topskill.chat import build_runtime_agent as chat_build_runtime_agent

    return await chat_build_runtime_agent(agent, version, channel="a2a")
