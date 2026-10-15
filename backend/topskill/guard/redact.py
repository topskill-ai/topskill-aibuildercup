"""Span redaction for SDP findings (SPEC §18 "Guard")."""

from __future__ import annotations

from collections.abc import Iterable

from topskill.guard.types import Finding


def placeholder(info_type: str) -> str:
    return f"[REDACTED:{info_type or 'SENSITIVE_DATA'}]"


def merge_spans(findings: Iterable[Finding], length: int) -> list[Finding]:
    """Clamp to ``[0, length]``, drop empty spans, merge overlapping ones (first info type wins)."""
    spans = sorted(
        (Finding(f.info_type, max(0, f.start), min(length, f.end)) for f in findings),
        key=lambda f: (f.start, -f.end),
    )
    merged: list[Finding] = []
    for span in spans:
        if span.end <= span.start:
            continue
        if merged and span.start < merged[-1].end:
            last = merged[-1]
            merged[-1] = Finding(last.info_type, last.start, max(last.end, span.end))
            continue
        merged.append(span)
    return merged


def redact(text: str, findings: Iterable[Finding]) -> str:
    """Replace every finding span of ``text`` with ``[REDACTED:<INFO_TYPE>]``."""
    out: list[str] = []
    pos = 0
    for span in merge_spans(findings, len(text)):
        out.append(text[pos : span.start])
        out.append(placeholder(span.info_type))
        pos = span.end
    out.append(text[pos:])
    return "".join(out)


def split_findings(findings: Iterable[Finding], parts: list[str], sep: str) -> list[list[Finding]]:
    """Map findings over ``sep.join(parts)`` to per-part findings (spans crossing a separator are cut)."""
    per_part: list[list[Finding]] = [[] for _ in parts]
    bounds: list[tuple[int, int]] = []
    pos = 0
    for part in parts:
        bounds.append((pos, pos + len(part)))
        pos += len(part) + len(sep)
    for f in findings:
        for i, (lo, hi) in enumerate(bounds):
            start, end = max(f.start, lo), min(f.end, hi)
            if start < end:
                per_part[i].append(Finding(f.info_type, start - lo, end - lo))
    return per_part
