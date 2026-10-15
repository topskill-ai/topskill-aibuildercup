"""Screening of A2A message parts (SPEC §18.2).

Every part the model would read is screened, not only text parts: the ADK converter turns a data part into
an inline ``text/plain`` blob (its JSON) and a file part into inline bytes or a file URI. With screening on:

* ``text`` parts: the text;
* data parts (v1.0 ``{"data": <any JSON>}``, v0.3 ``{"kind": "data", "data": {...}}``): their compact JSON
  (``ensure_ascii=False``, so the screened text is what the value says);
* inline file bytes (v1.0 ``raw``, v0.3 ``file.bytes``) with a ``text/*`` or JSON media type: the UTF-8
  text.

All screened segments are joined with ``\\n`` into one Model Armor check. A ``redact`` verdict is written back
into each segment (data: the redacted JSON must still parse to the same JSON type; bytes: re-encoded); when a
segment cannot be rewritten the message is blocked instead. Parts that cannot be screened (file URIs, binary
bytes, unknown shapes) fail closed: :func:`screen_a2a_message` reports them as ``invalid`` and the route
answers 400 ``validation_error`` before anything is reserved. The Agent Card only advertises ``text/plain``.
With screening off for the agent (or ``GUARD_BACKEND=off``) the message is left untouched.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from topskill.guard import stats
from topskill.guard.redact import redact, split_findings
from topskill.guard.service import sanitize_prompt, screening_enabled
from topskill.guard.types import GuardContext, GuardVerdict

SEP = "\n"
_JSON_MIME_SUFFIXES = ("/json", "+json")


@dataclass
class _Segment:
    text: str
    write: Callable[[str], bool]  # store the redacted text; False when it cannot be represented


@dataclass
class A2AScreen:
    """``invalid`` is set (and ``verdict`` is an unscreened allow) when a part cannot be screened."""

    verdict: GuardVerdict
    invalid: str | None = None
    segments: int = field(default=0)


class _Unscreenable(Exception):
    pass


def _screenable_mime(mime: Any) -> bool:
    if not isinstance(mime, str) or not mime.strip():
        return False
    base = mime.split(";", 1)[0].strip().lower()
    return base.startswith("text/") or base.endswith(_JSON_MIME_SUFFIXES)


def _b64decode(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii"))


def _text_segment(holder: dict, key: str) -> _Segment:
    value = holder[key]
    if not isinstance(value, str):
        raise _Unscreenable("text must be a string")

    def write(new: str) -> bool:
        holder[key] = new
        return True

    return _Segment(value, write)


def _data_segment(holder: dict, key: str) -> _Segment:
    value = holder[key]
    try:
        dumped = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise _Unscreenable("data is not JSON") from exc

    def write(new: str) -> bool:
        try:
            parsed = json.loads(new)
        except ValueError:
            return False
        if type(parsed) is not type(value):
            return False
        holder[key] = parsed
        return True

    return _Segment(dumped, write)


def _bytes_segment(holder: dict, key: str, mime: Any) -> _Segment:
    value = holder[key]
    if not isinstance(value, str) or not _screenable_mime(mime):
        raise _Unscreenable("binary file content cannot be screened")
    try:
        text = _b64decode(value).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise _Unscreenable("file content is not UTF-8 text") from exc

    def write(new: str) -> bool:
        holder[key] = base64.b64encode(new.encode("utf-8")).decode("ascii")
        return True

    return _Segment(text, write)


def _part_segments(part: Any) -> list[_Segment]:
    """Segments of one part (v1.0 and v0.3 shapes); raises :class:`_Unscreenable`."""
    if not isinstance(part, dict):
        raise _Unscreenable("part must be an object")
    kind = part.get("kind")
    if kind is not None:  # v0.3
        if kind == "text" and "text" in part:
            return [_text_segment(part, "text")]
        if kind == "data" and "data" in part:
            return [_data_segment(part, "data")]
        if kind == "file" and isinstance(part.get("file"), dict):
            file = part["file"]
            if "uri" in file:
                raise _Unscreenable("file URIs cannot be screened")
            if "bytes" in file:
                return [_bytes_segment(file, "bytes", file.get("mimeType") or file.get("mime_type"))]
        raise _Unscreenable("unsupported part")
    segments: list[_Segment] = []  # v1.0: every content field present is screened
    if "url" in part:
        raise _Unscreenable("file URLs cannot be screened")
    if "text" in part:
        segments.append(_text_segment(part, "text"))
    if "data" in part:
        segments.append(_data_segment(part, "data"))
    if "raw" in part:
        segments.append(_bytes_segment(part, "raw", part.get("mediaType") or part.get("media_type")))
    if not segments:
        raise _Unscreenable("unsupported part")
    return segments


def _apply(verdict: GuardVerdict, segments: list[_Segment]) -> GuardVerdict:
    """Write a ``redact`` verdict back into the parts; a block verdict when that is impossible."""
    texts = [s.text for s in segments]
    if verdict.findings:
        per_segment = split_findings(verdict.findings, texts, SEP)
        pairs = zip(segments, per_segment, strict=True)
        updates = [(seg, redact(seg.text, own)) for seg, own in pairs if own]
    elif len(segments) == 1:  # de-identified text without locations
        updates = [(segments[0], verdict.text)]
    else:
        updates = []
    ok = bool(updates) and all(seg.write(new) for seg, new in updates)
    if ok:
        return verdict
    return GuardVerdict(
        "block",
        verdict.text,
        verdict.categories,
        info_types=verdict.info_types,
        findings=verdict.findings,
    )


async def screen_a2a_message(
    agent_id: str, enabled: bool, message: dict, *, request_id: str | None = None
) -> A2AScreen:
    """Screen every part of an A2A message in place and record the counters (one write). Never raises."""
    allow = GuardVerdict("allow", "", screened=False)
    if not screening_enabled(enabled):
        return A2AScreen(allow)
    parts = message.get("parts")
    segments: list[_Segment] = []
    try:
        for part in parts if isinstance(parts, list) else []:
            segments.extend(_part_segments(part))
    except _Unscreenable as exc:
        return A2AScreen(allow, invalid=str(exc))
    ctx = GuardContext(agent_id=agent_id, channel="a2a", enabled=enabled, request_id=request_id)
    verdict = await sanitize_prompt(SEP.join(s.text for s in segments), ctx)
    if verdict.redacted:
        verdict = _apply(verdict, segments)
    stats.record(agent_id, [("prompt", verdict)])
    return A2AScreen(verdict, segments=len(segments))
