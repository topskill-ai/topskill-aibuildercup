"""Web chat runtime and the chat → billing/knowledge/records injection point (SPEC §10.4, §14.2)."""

from topskill.chat.agent_factory import SearchMode, build_llm_agent, build_runtime_agent
from topskill.chat.deps import Deps, get_deps
from topskill.chat.history import load_history
from topskill.chat.prompt import build_instruction
from topskill.chat.runtime import classify_outcome

__all__ = [
    "Deps",
    "SearchMode",
    "build_instruction",
    "build_llm_agent",
    "build_runtime_agent",
    "classify_outcome",
    "get_deps",
    "load_history",
]
