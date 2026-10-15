"""Maps ADK events of one web turn to SSE frames (SPEC §7.2, §7.3, §10.4 "Event mapping").

* ``partial=True`` text parts (never thought parts) → ``text_delta`` and the answer text. Text of
  non-partial events duplicates the partials and is ignored, except for a model response that streamed
  no partial text at all (non-streaming fallback), whose text is then emitted once.
* Function calls/responses only from ``partial=False`` events → ``tool_start`` / ``tool_end`` (matched by
  function-call id). ``google_search_agent`` (bypass mode) is shown as "Searched the web".
* ``grounding_metadata`` from every event → the turn's citation collector; new web-search queries become
  synthetic ``web-<i>`` tool lines (skipped for a turn that used ``google_search_agent``). In bypass mode
  ADK propagates the sub-agent's grounding metadata as a camelCase ``dict``; it is normalised here.
* Usage from the last event carrying ``usage_metadata``; model from ``model_version``.
* An event whose ``error_code`` is set and not ``"STOP"`` ends the turn with ``agent_run_failed``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from google.adk.events import Event
from google.genai import types
from pydantic import BaseModel

from topskill.domain import CitationCollector, MessageUsage, ToolKind, ToolLineDoc
from topskill.events import EventName, TextDeltaData, ToolEndData, ToolStartData

log = logging.getLogger(__name__)

TARGET_MAX = 80
SEARCH_AGENT_TOOL = "google_search_agent"
SAVE_RECORD_TOOL = "save_record"

Frame = tuple[str, BaseModel]

_TOOL_NAMES: dict[str, tuple[ToolKind, str]] = {
    "search_knowledge": ("search", "Searched the knowledge base"),
    "google_search": ("web", "Searched the web"),
    SEARCH_AGENT_TOOL: ("web", "Searched the web"),
    "read_skill": ("skill", "Read the playbook"),
    "read_skill_file": ("skill", "Read the playbook"),
    SAVE_RECORD_TOOL: ("record", "Saved notes"),
}
_SECRETISH_RE = re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password|bearer)\b\S*")


def clip_target(value: Any) -> str:
    """User-facing tool target: one line, ≤ 80 chars, no credentials-looking words."""
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    text = _SECRETISH_RE.sub("•••", text)
    return text if len(text) <= TARGET_MAX else text[: TARGET_MAX - 1].rstrip() + "…"


def humanize(name: str) -> str:
    words = re.sub(r"[_\-]+", " ", name).strip()
    return words[:1].upper() + words[1:] if words else "Tool"


def describe_tool(name: str, args: dict[str, Any] | None) -> tuple[ToolKind, str, str]:
    """``(kind, title, target)`` for a function call (SPEC §7.2 tool naming)."""
    args = args or {}
    kind, title = _TOOL_NAMES.get(name, ("other", humanize(name)))
    if name == "search_knowledge":
        target = clip_target(args.get("query"))
    elif name == SEARCH_AGENT_TOOL:
        target = clip_target(args.get("request"))
    elif name == "read_skill":
        target = clip_target(args.get("name"))
    elif name == "read_skill_file":
        skill, path = args.get("name"), args.get("path")
        target = clip_target(
            f"{skill} · {path}" if isinstance(skill, str) and isinstance(path, str) else skill
        )
    elif name == SAVE_RECORD_TOOL:
        fields = args.get("fields")
        n = len(fields) if isinstance(fields, dict) else 0
        target = f"{n} field{'s' if n != 1 else ''}"
        if args.get("complete") is True:
            target += " · completed"
    else:
        target = ""
    return kind, title, target


def normalize_grounding(gm: Any) -> types.GroundingMetadata | None:
    if gm is None or isinstance(gm, types.GroundingMetadata):
        return gm
    if isinstance(gm, dict):
        try:
            return types.GroundingMetadata.model_validate(gm)
        except Exception:  # malformed metadata must never break a turn
            log.warning("grounding_metadata_unparseable keys=%s", list(gm)[:8])
            return None
    return None


def _response_failed(response: Any) -> bool:
    if isinstance(response, dict):
        if response.get("ok") is False:
            return True
        if response.get("error") and "results" not in response:
            return True
    return False


@dataclass
class TurnState:
    """Accumulates what one turn produced. ``map_event`` returns the frames to emit."""

    collector: CitationCollector
    answer_parts: list[str] = field(default_factory=list)
    tools: dict[str, ToolLineDoc] = field(default_factory=dict)  # insertion-ordered by start
    usage: MessageUsage | None = None
    model: str | None = None
    visible: bool = False  # anything shown to the visitor yet (text or a tool line)
    error_code: str | None = None  # ADK error event (SAFETY, MAX_TOKENS, …)
    error_message: str | None = None
    called_tools: set[str] = field(default_factory=set)
    _partial_text_since_final: bool = False
    _web_lines: int = 0
    _anon_calls: int = 0

    @property
    def answer_text(self) -> str:
        return "".join(self.answer_parts)

    @property
    def used_search_agent(self) -> bool:
        return SEARCH_AGENT_TOOL in self.called_tools

    def _text(self, parts: list[types.Part]) -> str:
        return "".join(p.text for p in parts if p.text and not p.thought)

    def _delta(self, text: str) -> list[Frame]:
        if not text:
            return []
        self.answer_parts.append(text)
        self.visible = True
        return [(EventName.TEXT_DELTA, TextDeltaData(delta=text))]

    def _start_tool(self, call_id: str, name: str, args: dict[str, Any] | None) -> list[Frame]:
        kind, title, target = describe_tool(name, args)
        self.tools[call_id] = ToolLineDoc(
            id=call_id, tool=name, kind=kind, title=title, target=target, status="running"
        )
        self.called_tools.add(name)
        self.visible = True
        return [
            (
                EventName.TOOL_START,
                ToolStartData(id=call_id, tool=name, kind=kind, title=title, target=target),
            )
        ]

    def _end_tool(self, call_id: str, name: str, failed: bool) -> list[Frame]:
        line = self.tools.get(call_id)
        if line is None or line.status != "running":
            return []
        status = "error" if failed else "completed"
        self.tools[call_id] = line.model_copy(update={"status": status})
        return [(EventName.TOOL_END, ToolEndData(id=call_id, tool=line.tool, status=status))]

    def _grounding(self, gm: types.GroundingMetadata | None) -> list[Frame]:
        if gm is None:
            return []
        new_queries = self.collector.add_grounding(gm)
        if self.used_search_agent:
            return []
        frames: list[Frame] = []
        for query in new_queries:
            line_id = f"web-{self._web_lines}"
            self._web_lines += 1
            target = clip_target(query)
            self.tools[line_id] = ToolLineDoc(
                id=line_id,
                tool="google_search",
                kind="web",
                title="Searched the web",
                target=target,
                status="completed",
            )
            self.visible = True
            frames.append(
                (
                    EventName.TOOL_START,
                    ToolStartData(
                        id=line_id, tool="google_search", kind="web", title="Searched the web", target=target
                    ),
                )
            )
            frames.append(
                (EventName.TOOL_END, ToolEndData(id=line_id, tool="google_search", status="completed"))
            )
        return frames

    def map_event(self, ev: Event) -> list[Frame]:
        frames: list[Frame] = []
        if ev.model_version:
            self.model = ev.model_version
        if ev.usage_metadata is not None:
            um = ev.usage_metadata
            self.usage = MessageUsage(
                input_tokens=um.prompt_token_count,
                output_tokens=um.candidates_token_count,
                model=ev.model_version or self.model,
            )
        parts = list(ev.content.parts or []) if ev.content else []
        if ev.partial:
            # A function call that already shows up in a partial event is announced from the final one.
            if any(fc.name == SEARCH_AGENT_TOOL for fc in ev.get_function_calls()):
                self.called_tools.add(SEARCH_AGENT_TOOL)
            text = self._text(parts)
            if text:
                self._partial_text_since_final = True
                frames += self._delta(text)
        else:
            if ev.author != "user":
                text = self._text(parts)
                if text and not self._partial_text_since_final:
                    frames += self._delta(text)
                if ev.content is not None and ev.author != "user":
                    self._partial_text_since_final = False
            for fc in ev.get_function_calls():
                call_id = fc.id or f"call-{self._anon_calls}"
                if not fc.id:
                    self._anon_calls += 1
                if call_id not in self.tools:
                    frames += self._start_tool(call_id, fc.name or "tool", fc.args)
            for fr in ev.get_function_responses():
                call_id = fr.id or next(
                    (k for k, v in self.tools.items() if v.tool == fr.name and v.status == "running"), ""
                )
                frames += self._end_tool(call_id, fr.name or "tool", _response_failed(fr.response))
        frames += self._grounding(normalize_grounding(ev.grounding_metadata))
        if ev.error_code and ev.error_code != "STOP":
            self.error_code = str(ev.error_code)
            self.error_message = ev.error_message
        return frames

    def close_unfinished(self, *, interrupted: bool) -> list[Frame]:
        """Close every running tool: wire ``tool_end{error}``; stored as interrupted/error."""
        frames: list[Frame] = []
        for call_id, line in list(self.tools.items()):
            if line.status != "running":
                continue
            self.tools[call_id] = line.model_copy(
                update={"status": "interrupted" if interrupted else "error"}
            )
            frames.append((EventName.TOOL_END, ToolEndData(id=call_id, tool=line.tool, status="error")))
        return frames

    def tool_lines(self) -> list[ToolLineDoc]:
        return list(self.tools.values())
