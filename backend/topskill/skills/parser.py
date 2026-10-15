"""Skill package parsing (SPEC §6.4 Skills, §8.7).

Pure functions, no I/O: front matter, slugify, path normalisation and a defensive zip reader.

Limits (all on uncompressed sizes): at most 30 files, 2 MB per file, 10 MB in total, ``SKILL.md`` at most
200 KB. Exactly one ``SKILL.md`` (case-insensitive) must exist at the package root after a single common
top-level directory is stripped. Hidden files (``.`` prefix in any segment) and ``__MACOSX/`` are skipped;
absolute paths, ``..`` and backslashes are rejected.
"""

from __future__ import annotations

import io
import mimetypes
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import yaml

from topskill.errors import ApiError, Codes

MAX_FILES = 30
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
MAX_SKILL_MD_BYTES = 200 * 1024
MAX_REQUEST_BYTES = 12 * 1024 * 1024
MAX_ZIP_ENTRIES = 200
NAME_MAX = 100
DESCRIPTION_MAX = 300
SKILL_MD = "SKILL.md"

TEXT_EXTENSIONS = frozenset(
    {
        ".md",
        ".markdown",
        ".txt",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".html",
        ".htm",
        ".py",
        ".js",
        ".ts",
        ".sh",
        ".toml",
        ".ini",
        ".cfg",
        ".rst",
        ".sql",
    }
)
_MIME_OVERRIDES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".toml": "application/toml",
}
_ZIP_READ_ERRORS = (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, OSError)
_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def skill_invalid(message: str, **detail: object) -> ApiError:
    return ApiError(400, Codes.SKILL_INVALID, message, detail or None)


def too_large(message: str, **detail: object) -> ApiError:
    return ApiError(413, Codes.PAYLOAD_TOO_LARGE, message, detail or None)


# --------------------------------------------------------------------------- text helpers


def slugify(text: str, *, max_len: int | None = None) -> str:
    """ASCII fold, lower case, non-alphanumerics → ``-``, collapsed and trimmed (may return "")."""
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP_RE.sub("-", folded.lower()).strip("-")
    if max_len is not None:
        slug = slug[:max_len].strip("-")
    return slug


def clip(text: str, limit: int) -> str:
    """Clip to ``limit`` characters, ending with an ellipsis when something was cut."""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def humanize(slug: str) -> str:
    """``visa-advisor`` → ``Visa Advisor``."""
    words = [w for w in re.split(r"[-_\s]+", slug) if w]
    return " ".join(w[:1].upper() + w[1:] for w in words)


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """YAML front matter between leading ``---`` lines → ``(metadata, body)``.

    Only ``name`` and ``description`` are kept (as strings). Any parse failure yields empty metadata and
    the whole text as the body; it is never an error.
    """
    text = text.lstrip("﻿")
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    body = text[match.end() :]
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}, body
    if not isinstance(data, dict):
        return {}, body
    meta: dict[str, str] = {}
    for key in ("name", "description"):
        value = data.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            meta[key] = " ".join(str(value).split())
    return meta, body


def guess_mime(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in _MIME_OVERRIDES:
        return _MIME_OVERRIDES[suffix]
    mime, _ = mimetypes.guess_type(path)
    return mime or "application/octet-stream"


def is_text_file(path: str, data: bytes) -> bool:
    """Text when the extension is a known text type (or the content looks like UTF-8 text)."""
    suffix = PurePosixPath(path).suffix.lower()
    if b"\x00" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return suffix in TEXT_EXTENSIONS or suffix == "" or guess_mime(path).startswith("text/")


# --------------------------------------------------------------------------- paths


def clean_path(raw: str) -> str | None:
    """Validate one relative path of the package. ``None`` = skip (hidden file, ``__MACOSX``, directory).

    Raises ``skill_invalid`` for absolute paths, ``..`` segments, backslashes or control characters.
    """
    if raw is None:
        raise skill_invalid("A file has no name.")
    if "\\" in raw:
        raise skill_invalid("File paths must not contain backslashes.", path=raw)
    if any(ord(ch) < 32 for ch in raw):
        raise skill_invalid("File paths must not contain control characters.", path=raw)
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise skill_invalid("File paths must be relative.", path=raw)
    if raw.endswith("/"):
        return None  # directory entry
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts:
        return None
    if any(p == ".." for p in parts):
        raise skill_invalid("File paths must not contain '..'.", path=raw)
    if parts[0] == "__MACOSX" or any(p.startswith(".") for p in parts):
        return None
    return "/".join(parts)


# --------------------------------------------------------------------------- package


@dataclass
class SkillPackage:
    """A validated package: files keyed by relative path; ``SKILL.md`` is always at the root as
    ``SKILL.md``."""

    files: dict[str, bytes]
    dir_name: str | None
    name: str
    slug: str
    description: str
    body: str
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def total_bytes(self) -> int:
        return sum(len(v) for v in self.files.values())


def read_zip(data: bytes) -> list[tuple[str, bytes]]:
    """Read a zip archive defensively → ``[(raw path, bytes)]`` (paths not yet cleaned).

    Rejects archives with more than 200 entries before reading anything; checks each declared size and
    the running uncompressed total against the limits before reading; reads members with
    ``zf.open(info).read(limit + 1)`` and rejects anything longer than the limit or than its declared size
    (lying headers); rejects symlink entries; never extracts to disk.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise skill_invalid("The zip archive could not be read.") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise skill_invalid(
                f"The archive has too many entries (max {MAX_ZIP_ENTRIES}).", entries=len(infos)
            )
        selected: list[tuple[zipfile.ZipInfo, str]] = []
        declared_total = 0
        for info in infos:
            if info.is_dir():
                continue
            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise skill_invalid("Symbolic links are not allowed in a skill package.", path=info.filename)
            path = clean_path(info.filename)
            if path is None:
                continue
            limit = _member_limit(path)
            if info.file_size > limit:
                raise too_large(f"'{path}' is larger than {limit // 1024} KB.", path=path)
            declared_total += info.file_size
            if declared_total > MAX_TOTAL_BYTES:
                raise too_large("The skill package is larger than 10 MB uncompressed.")
            selected.append((info, path))
        if len(selected) > MAX_FILES:
            raise skill_invalid(f"A skill package can have at most {MAX_FILES} files.", files=len(selected))
        out: list[tuple[str, bytes]] = []
        actual_total = 0
        for info, path in selected:
            limit = _member_limit(path)
            try:
                with zf.open(info) as fh:
                    content = fh.read(limit + 1)
            except _ZIP_READ_ERRORS as exc:
                raise skill_invalid("The zip archive is damaged or uses an unsupported format.") from exc
            except EOFError as exc:
                raise skill_invalid("The zip archive is truncated.") from exc
            if len(content) > limit or len(content) != info.file_size:
                raise skill_invalid("The zip archive has an inconsistent file size.", path=path)
            actual_total += len(content)
            if actual_total > MAX_TOTAL_BYTES:
                raise too_large("The skill package is larger than 10 MB uncompressed.")
            out.append((info.filename, content))
        return out


def _member_limit(path: str) -> int:
    return MAX_SKILL_MD_BYTES if PurePosixPath(path).name.lower() == "skill.md" else MAX_FILE_BYTES


def build_package(entries: list[tuple[str, bytes]], *, fallback_name: str | None = None) -> SkillPackage:
    """Validate raw ``(path, bytes)`` entries into a :class:`SkillPackage`.

    ``fallback_name`` (e.g. the zip file name) is used for the skill slug only when there is no common
    top-level directory and no front-matter ``name``.
    """
    cleaned: dict[str, bytes] = {}
    for raw, data in entries:
        path = clean_path(raw)
        if path is None:
            continue
        if path in cleaned:
            raise skill_invalid("The package contains the same file twice.", path=path)
        cleaned[path] = data
    if not cleaned:
        raise skill_invalid("The upload contains no files. Add a SKILL.md.")

    # Strip a single common top-level directory.
    dir_name: str | None = None
    firsts = {p.split("/", 1)[0] for p in cleaned}
    if len(firsts) == 1 and all("/" in p for p in cleaned):
        dir_name = next(iter(firsts))
        cleaned = {p.split("/", 1)[1]: d for p, d in cleaned.items()}

    if len(cleaned) > MAX_FILES:
        raise skill_invalid(f"A skill package can have at most {MAX_FILES} files.", files=len(cleaned))
    total = 0
    for path, data in cleaned.items():
        limit = _member_limit(path)
        if len(data) > limit:
            raise too_large(f"'{path}' is larger than {limit // 1024} KB.", path=path)
        total += len(data)
    if total > MAX_TOTAL_BYTES:
        raise too_large("The skill package is larger than 10 MB.")

    roots = [p for p in cleaned if "/" not in p and p.lower() == "skill.md"]
    if not roots:
        raise skill_invalid("The package needs a SKILL.md at its root.")
    if len(roots) > 1:
        raise skill_invalid("The package has more than one SKILL.md at its root.")
    root = roots[0]
    skill_bytes = cleaned.pop(root)
    try:
        text = skill_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise skill_invalid("SKILL.md must be UTF-8 text.") from exc
    if "\x00" in text:
        raise skill_invalid("SKILL.md must be UTF-8 text.")
    meta, body = parse_front_matter(text)
    if not body.strip():
        raise skill_invalid("SKILL.md has no instructions.")

    slug = (
        slugify(dir_name or "", max_len=60)
        or slugify(meta.get("name", ""), max_len=60)
        or slugify(fallback_name or "", max_len=60)
        or "skill"
    )
    raw_name = meta.get("name") or ""
    if not raw_name or (" " not in raw_name and raw_name == slugify(raw_name)):
        name = humanize(slugify(raw_name) or slug)
    else:
        name = raw_name
    files = {SKILL_MD: skill_bytes, **dict(sorted(cleaned.items()))}
    return SkillPackage(
        files=files,
        dir_name=dir_name,
        name=clip(name, NAME_MAX),
        slug=slug,
        description=clip(meta.get("description", ""), DESCRIPTION_MAX),
        body=body.strip(),
        meta=meta,
    )
