"""Skill playbooks for the chat runtime (SPEC §10.4 prompt section 3).

Skills are immutable, so their files are cached in-process by ``(skill_id, path)``. When the SKILL.md
bodies of an agent fit :data:`INLINE_BUDGET_CHARS`, the prompt inlines them and only reference files are
read through ``read_skill_file``; otherwise the prompt carries an index and the model pages through
``read_skill`` / ``read_skill_file`` (8 000-character pages with "Next page: offset=N" hints).
Paths are resolved only inside the skill package: absolute paths, ``..`` and backslashes are rejected and
the path must be one of the package's files.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict

from google.adk.tools import FunctionTool

from topskill.domain import SkillFile, SkillRef
from topskill.errors import ApiError
from topskill.storage import get_blob_store

log = logging.getLogger(__name__)

INLINE_BUDGET_CHARS = 12_000
PAGE_CHARS = 8_000
SKILL_MD = "SKILL.md"
_CACHE_MAX = 256

_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_FRONT_MATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*(?:\n|\Z)", re.DOTALL)


def skills_fit_inline(skills: list[SkillRef]) -> bool:
    return sum(s.body_chars for s in skills) <= INLINE_BUDGET_CHARS


def strip_front_matter(body: str) -> str:
    return _FRONT_MATTER_RE.sub("", body, count=1).strip()


def reference_files(ref: SkillRef) -> list[SkillFile]:
    """Every file of the package except SKILL.md itself."""
    return [f for f in ref.files if f.path.lower() != SKILL_MD.lower()]


def safe_path(path: str) -> str | None:
    """Normalised relative path, or None when it could escape the package."""
    path = (path or "").strip()
    if not path or path.startswith("/") or "\\" in path or ":" in path.split("/")[0]:
        return None
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


async def read_file_bytes(ref: SkillRef, path: str) -> bytes:
    """A file of an immutable skill package (cached). Raises ``ApiError(404)`` when missing."""
    key = (ref.skill_id, path)
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached
    data = await get_blob_store().get(ref.gcs_prefix + path)
    _cache[key] = data
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return data


async def read_skill_body(ref: SkillRef) -> str:
    """SKILL.md body without its YAML front matter ("" if unreadable)."""
    md = next((f for f in ref.files if f.path.lower() == SKILL_MD.lower()), None)
    try:
        data = await read_file_bytes(ref, md.path if md else SKILL_MD)
    except ApiError:
        log.warning("skill_body_missing skill_id=%s", ref.skill_id)
        return ""
    return strip_front_matter(data.decode("utf-8", errors="replace"))


def _page(content: str, offset: int) -> dict:
    offset = max(0, int(offset or 0))
    chunk = content[offset : offset + PAGE_CHARS]
    end = offset + len(chunk)
    result: dict = {"content": chunk, "offset": offset, "total_chars": len(content)}
    if end < len(content):
        result["next"] = f"Next page: offset={end}"
    return result


def find_skill(skills: list[SkillRef], name: str) -> SkillRef | None:
    wanted = (name or "").strip().lower()
    for ref in skills:
        if wanted in (ref.name.lower(), ref.slug.lower(), ref.skill_id.lower()):
            return ref
    if len(skills) == 1 and not wanted:
        return skills[0]
    return None


def make_skill_tools(skills: list[SkillRef], *, inline: bool) -> list[FunctionTool]:
    """``read_skill_file`` when any reference file exists; plus ``read_skill`` when not inlined."""
    tools: list[FunctionTool] = []
    names = ", ".join(s.name for s in skills)

    async def read_skill(name: str, offset: int = 0) -> dict:
        """Read the playbook (SKILL.md) of one of your skills, page by page.

        Args:
          name: The skill name as listed in your instructions.
          offset: Character offset of the page to read (0 for the first page).
        """
        ref = find_skill(skills, name)
        if ref is None:
            return {"error": f"Unknown skill. Available: {names}"}
        body = await read_skill_body(ref)
        return {"skill": ref.name, **_page(body, offset)}

    async def read_skill_file(name: str, path: str, offset: int = 0) -> dict:
        """Read a reference file that belongs to one of your skills, page by page.

        Args:
          name: The skill name as listed in your instructions.
          path: The file path relative to the skill package, exactly as listed.
          offset: Character offset of the page to read (0 for the first page).
        """
        ref = find_skill(skills, name)
        if ref is None:
            return {"error": f"Unknown skill. Available: {names}"}
        rel = safe_path(path)
        entry = next((f for f in ref.files if f.path == rel), None) if rel else None
        if entry is None:
            return {"error": "No such file in this skill."}
        if not entry.is_text:
            return {"skill": ref.name, "path": entry.path, "content": "binary file, not readable"}
        try:
            data = await read_file_bytes(ref, entry.path)
        except ApiError:
            return {"error": "The file could not be read."}
        content = data.decode("utf-8", errors="replace")
        if entry.path.lower() == SKILL_MD.lower():
            content = strip_front_matter(content)
        return {"skill": ref.name, "path": entry.path, **_page(content, offset)}

    if not inline:
        tools.append(FunctionTool(read_skill))
    if not inline or any(reference_files(s) for s in skills):
        tools.append(FunctionTool(read_skill_file))
    return tools
