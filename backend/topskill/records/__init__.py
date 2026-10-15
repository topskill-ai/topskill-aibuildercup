"""Structured records collected while visitors chat (SPEC §10.3, §14.2).

Other packages import only the names re-exported here (through ``chat.deps.get_deps()`` for chat/a2a).
"""

from __future__ import annotations

from topskill.domain import SaveResult
from topskill.records.extract import extract
from topskill.records.schema import validate_schema
from topskill.records.service import apply_values, assert_schema_change_allowed
from topskill.records.tool import make_save_record_tool, prompt_section

is_stub = False

__all__ = [
    "SaveResult",
    "apply_values",
    "assert_schema_change_allowed",
    "extract",
    "is_stub",
    "make_save_record_tool",
    "prompt_section",
    "validate_schema",
]
