"""Skill package parsing: front matter, paths, limits and the defensive zip reader (unit)."""

from __future__ import annotations

import io
import stat
import struct
import zipfile

import pytest

from topskill.errors import ApiError
from topskill.skills.parser import (
    MAX_FILES,
    build_package,
    clean_path,
    clip,
    humanize,
    parse_front_matter,
    read_zip,
    slugify,
)

pytestmark = pytest.mark.unit

SKILL = b"---\nname: visa-advisor\ndescription: Plans work visas.\n---\n# Playbook\nDo the thing.\n"


def make_zip(entries: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compression) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_front_matter_parsed_and_stripped() -> None:
    meta, body = parse_front_matter(SKILL.decode())
    assert meta == {"name": "visa-advisor", "description": "Plans work visas."}
    assert body.startswith("# Playbook")


@pytest.mark.parametrize(
    "text",
    [
        "# No front matter\nbody",
        "---\nname: [unclosed\n---\nbody",  # YAML error → empty metadata, not an error
        "---\n- a\n- b\n---\nbody",  # not a mapping
    ],
)
def test_front_matter_failures_are_not_errors(text: str) -> None:
    meta, body = parse_front_matter(text)
    assert meta == {}
    assert "body" in body


def test_front_matter_ignores_other_keys_and_bom() -> None:
    meta, _ = parse_front_matter("\ufeff---\nname: X\nlicense: MIT\nallowed-tools: [a]\n---\nhi")
    assert meta == {"name": "X"}


def test_slugify_and_helpers() -> None:
    assert slugify("Visa Advisor – São Paulo!") == "visa-advisor-sao-paulo"
    assert slugify("  --  ") == ""
    assert slugify("a" * 80, max_len=50) == "a" * 50
    assert humanize("visa-advisor") == "Visa Advisor"
    assert clip("abcdef", 4) == "abc…" and clip("abc", 4) == "abc"


@pytest.mark.parametrize("raw", ["../evil.md", "a/../../b.md", "/etc/passwd", "C:/x.md", "a\\b.md", "a\x01b"])
def test_clean_path_rejects_unsafe(raw: str) -> None:
    with pytest.raises(ApiError) as exc:
        clean_path(raw)
    assert exc.value.code == "skill_invalid"


@pytest.mark.parametrize("raw", ["__MACOSX/x/SKILL.md", ".git/config", "a/.DS_Store", "dir/"])
def test_clean_path_skips_hidden_and_dirs(raw: str) -> None:
    assert clean_path(raw) is None


def test_package_strips_single_top_dir_and_names_skill() -> None:
    pkg = build_package(
        [
            ("visa-advisor/SKILL.md", SKILL),
            ("visa-advisor/references/checklist.md", b"# list"),
            ("visa-advisor/.DS_Store", b"junk"),
            ("__MACOSX/visa-advisor/._SKILL.md", b"junk"),
        ]
    )
    assert list(pkg.files) == ["SKILL.md", "references/checklist.md"]
    assert pkg.dir_name == "visa-advisor"
    assert pkg.slug == "visa-advisor"
    assert pkg.name == "Visa Advisor"
    assert pkg.description == "Plans work visas."
    assert pkg.body.startswith("# Playbook")


def test_package_root_skill_md_is_case_insensitive_and_renamed() -> None:
    pkg = build_package([("skill.md", b"# Just a body"), ("notes.txt", b"n")])
    assert "SKILL.md" in pkg.files and pkg.slug == "skill" and pkg.name == "Skill"
    pkg2 = build_package([("Skill.MD", b"---\nname: Contract Reviewer Pro\n---\nbody")])
    assert pkg2.name == "Contract Reviewer Pro" and pkg2.slug == "contract-reviewer-pro"


@pytest.mark.parametrize(
    ("entries", "code"),
    [
        ([("README.md", b"x")], "skill_invalid"),  # no SKILL.md
        ([("docs/SKILL.md", b"x"), ("other/README.md", b"y")], "skill_invalid"),  # not at the root
        ([("SKILL.md", b"x"), ("skill.md", b"y")], "skill_invalid"),  # two at the root
        ([("SKILL.md", b"---\nname: x\n---\n   ")], "skill_invalid"),  # empty body
        ([("SKILL.md", b"\xff\xfe\x00bad")], "skill_invalid"),  # not UTF-8
        ([("SKILL.md", b"x" * (200 * 1024 + 1))], "payload_too_large"),
        ([("SKILL.md", b"x"), ("big.bin", b"0" * (2 * 1024 * 1024 + 1))], "payload_too_large"),
        ([("SKILL.md", b"x")] + [(f"f{i}.md", b"x") for i in range(MAX_FILES)], "skill_invalid"),
    ],
)
def test_package_rejections(entries: list[tuple[str, bytes]], code: str) -> None:
    with pytest.raises(ApiError) as exc:
        build_package(entries)
    assert exc.value.code == code


def test_package_total_size_limit() -> None:
    entries = [("SKILL.md", b"x")] + [(f"f{i}.bin", b"0" * (2 * 1024 * 1024 - 10)) for i in range(6)]
    with pytest.raises(ApiError) as exc:
        build_package(entries)
    assert exc.value.code == "payload_too_large"


def test_zip_with_single_top_dir() -> None:
    data = make_zip({"visa-advisor/SKILL.md": SKILL, "visa-advisor/references/x.md": b"ref"})
    pkg = build_package(read_zip(data), fallback_name="upload")
    assert sorted(pkg.files) == ["SKILL.md", "references/x.md"] and pkg.slug == "visa-advisor"


def test_zip_without_top_dir_uses_front_matter_then_fallback() -> None:
    assert build_package(read_zip(make_zip({"SKILL.md": SKILL}))).slug == "visa-advisor"
    pkg = build_package(read_zip(make_zip({"SKILL.md": b"# body"})), fallback_name="My Zip")
    assert pkg.slug == "my-zip"


def test_zip_path_traversal_rejected() -> None:
    data = make_zip({"SKILL.md": SKILL, "../../evil.md": b"x"})
    with pytest.raises(ApiError) as exc:
        read_zip(data)
    assert exc.value.code == "skill_invalid"


def test_zip_bomb_rejected_by_declared_uncompressed_size() -> None:
    # 11 MB of zeros compresses to a few KB; rejected from the header before anything is read.
    data = make_zip({"SKILL.md": SKILL, "bomb.bin": b"\0" * (11 * 1024 * 1024)})
    assert len(data) < 100_000
    with pytest.raises(ApiError) as exc:
        read_zip(data)
    assert exc.value.code == "payload_too_large"


def test_zip_bomb_rejected_by_running_total() -> None:
    entries = {"SKILL.md": SKILL} | {f"part{i}.bin": b"\0" * (1900 * 1024) for i in range(6)}
    with pytest.raises(ApiError) as exc:
        read_zip(make_zip(entries))
    assert exc.value.code == "payload_too_large"


def _patch_declared_size(data: bytes, name: bytes, new_size: int) -> bytes:
    """Rewrite the uncompressed-size fields of one member in both the local and central headers."""
    buf = bytearray(data)
    pos = 0
    while (pos := buf.find(b"PK\x03\x04", pos)) != -1:  # local file header: size at +22
        name_len = struct.unpack_from("<H", buf, pos + 26)[0]
        if bytes(buf[pos + 30 : pos + 30 + name_len]) == name:
            struct.pack_into("<I", buf, pos + 22, new_size)
        pos += 4
    pos = 0
    while (pos := buf.find(b"PK\x01\x02", pos)) != -1:  # central directory: size at +24
        name_len = struct.unpack_from("<H", buf, pos + 28)[0]
        if bytes(buf[pos + 46 : pos + 46 + name_len]) == name:
            struct.pack_into("<I", buf, pos + 24, new_size)
        pos += 4
    return bytes(buf)


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_zip_lying_header_rejected(compression: int) -> None:
    real = b"A" * (3 * 1024 * 1024)  # 3 MB > 2 MB per-file limit
    data = make_zip({"SKILL.md": SKILL, "liar.txt": real}, compression=compression)
    lying = _patch_declared_size(data, b"liar.txt", 10)
    assert zipfile.ZipFile(io.BytesIO(lying)).getinfo("liar.txt").file_size == 10
    with pytest.raises(ApiError) as exc:
        read_zip(lying)
    assert exc.value.code == "skill_invalid"


def test_zip_symlink_entry_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", SKILL)
        link = zipfile.ZipInfo("references/passwd")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "/etc/passwd")
    with pytest.raises(ApiError) as exc:
        read_zip(buf.getvalue())
    assert exc.value.code == "skill_invalid"
    assert exc.value.detail == {"path": "references/passwd"}


def test_zip_with_too_many_entries_rejected_before_reading() -> None:
    entries = {"SKILL.md": SKILL} | {f"e{i}.md": b"" for i in range(200)}
    with pytest.raises(ApiError) as exc:
        read_zip(make_zip(entries))
    assert exc.value.code == "skill_invalid"
    assert exc.value.detail == {"entries": 201}


def test_zip_garbage_rejected() -> None:
    with pytest.raises(ApiError) as exc:
        read_zip(b"not a zip at all")
    assert exc.value.code == "skill_invalid"
