from __future__ import annotations

import io
import struct
import tracemalloc
import zipfile

import pytest

from tests.knowledge.docs import make_docx, make_pdf
from topskill.errors import ApiError
from topskill.knowledge.base import (
    TOO_LARGE_ERROR,
    UNPARSEABLE_ERROR,
    clean_filename,
    display_name,
    file_type,
    parse_display_name,
)
from topskill.knowledge.extract import FileTooLarge, UnparseableFile, extract_text, split_sections

pytestmark = pytest.mark.unit


def test_pdf_pages_are_real_pages() -> None:
    text = extract_text(make_pdf(), "application/pdf")
    assert text.real_pages and text.page_count == 3 and text.has_text
    assert "Priority service costs 480 Zorkmids" in text.pages[1]
    assert "Refund requests" in text.pages[2]


def test_pdf_without_text_is_detected() -> None:
    text = extract_text(make_pdf([], blank_pages=2), "application/pdf")
    assert text.page_count == 2 and not text.has_text


def test_corrupt_pdf_raises() -> None:
    with pytest.raises(UnparseableFile):
        extract_text(b"%PDF-1.4 not really a pdf", "application/pdf")


def test_docx_and_text_are_split_into_sections() -> None:
    paragraphs = [f"Paragraph {i}: " + "word " * 60 for i in range(10)]
    text = extract_text(make_docx(paragraphs), file_type("notes.docx"))
    assert not text.real_pages and text.page_count is None
    assert len(text.pages) >= 3 and all(len(p) <= 1200 for p in text.pages)
    plain = extract_text(("line one\r\nline two\n" * 200).encode("utf-8-sig"), "text/plain")
    assert all(len(p) <= 1200 for p in plain.pages) and plain.pages[0].startswith("line one\nline two")
    with pytest.raises(UnparseableFile):
        extract_text(b"PK\x03\x04garbage", file_type("bad.docx"))


def test_latin1_text_does_not_crash() -> None:
    text = extract_text("café olé".encode("latin-1"), "text/csv")
    assert text.has_text


def test_split_sections_long_line_and_boundaries() -> None:
    long_line = "abc " * 1000
    sections = split_sections(long_line, 100)
    assert all(0 < len(s) <= 100 for s in sections)
    assert "".join(s.replace(" ", "") for s in sections) == long_line.replace(" ", "")
    assert split_sections("   ") == []


def test_file_types_and_names() -> None:
    assert file_type("Guide.PDF") == "application/pdf"
    with pytest.raises(ApiError) as err:
        file_type("evil.exe")
    assert err.value.status == 400 and err.value.code == "unsupported_file_type"
    assert clean_filename("../../etc/passwd.md") == "passwd.md"
    assert clean_filename("C:\\docs\\a<b>.pdf") == "ab.pdf"
    assert clean_filename("") == "document"
    long_name = "x" * 300 + ".pdf"
    assert len(clean_filename(long_name)) == 120 and clean_filename(long_name).endswith(".pdf")
    assert parse_display_name(display_name("kf_0123", "a__b.pdf")) == ("kf_0123", "a__b.pdf")
    assert parse_display_name("other.pdf") == (None, "other.pdf")
    assert parse_display_name(None) == (None, None)


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _zip_with_large_part(size: int, *, entries: int = 0) -> bytes:
    """A zip whose ``word/document.xml`` inflates to ``size`` bytes (written in 1 MB chunks)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        with zf.open("word/document.xml", "w", force_zip64=True) as part:
            block = b"<w:p><w:r><w:t>aaaa</w:t></w:r></w:p>" * (1024 * 1024 // 38)
            written = 0
            while written < size:
                chunk = block[: size - written]
                part.write(chunk)
                written += len(chunk)
        for i in range(entries):
            zf.writestr(f"customXml/item{i}.xml", b"<x/>")
    return buf.getvalue()


def test_docx_zip_bomb_is_rejected_without_inflating() -> None:
    bomb = _zip_with_large_part(60 * 1024 * 1024)
    assert len(bomb) < 1024 * 1024  # a small upload ...
    tracemalloc.start()
    try:
        with pytest.raises(UnparseableFile):  # ... that would inflate to 60 MB of XML
            extract_text(bomb, DOCX)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 5 * 1024 * 1024


def test_docx_high_ratio_part_and_too_many_entries_are_rejected() -> None:
    with pytest.raises(UnparseableFile) as err:
        extract_text(_zip_with_large_part(5 * 1024 * 1024), DOCX)  # compresses far beyond 100:1
    assert "compressed beyond" in str(err.value)
    with pytest.raises(UnparseableFile) as err:
        extract_text(_zip_with_large_part(1000, entries=250), DOCX)
    assert "zip entries" in str(err.value)


def _patch_sizes(data: bytes, name: bytes, real: int, fake: int) -> bytes:
    """Rewrite the declared uncompressed size of ``name`` in the local and central headers."""
    out = bytearray(data)
    real_b, fake_b = struct.pack("<I", real), struct.pack("<I", fake)
    for sig, size_off, name_off in ((b"PK\x03\x04", 22, 30), (b"PK\x01\x02", 24, 46)):
        pos = out.find(sig)
        while pos >= 0:
            if out[pos + name_off : pos + name_off + len(name)] == name:
                assert out[pos + size_off : pos + size_off + 4] == real_b
                out[pos + size_off : pos + size_off + 4] = fake_b
            pos = out.find(sig, pos + 4)
    return bytes(out)


def test_docx_with_lying_size_header_is_rejected() -> None:
    real = make_docx(["The real paragraph " * 200])
    with zipfile.ZipFile(io.BytesIO(real)) as zf:
        size = zf.getinfo("word/document.xml").file_size
    lying = _patch_sizes(real, b"word/document.xml", size, 100)
    with pytest.raises(UnparseableFile):
        extract_text(lying, DOCX)


def test_extraction_output_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    from topskill.knowledge import extract

    monkeypatch.setattr(extract, "MAX_EXTRACTED_CHARS", 500)
    with pytest.raises(FileTooLarge) as err:
        extract_text(make_pdf(), "application/pdf")
    assert err.value.user_message == TOO_LARGE_ERROR
    with pytest.raises(FileTooLarge):
        extract_text(b"x " * 400, "text/plain")
    with pytest.raises(FileTooLarge):
        extract_text(make_docx(["word " * 200]), DOCX)
    monkeypatch.setattr(extract, "MAX_EXTRACTED_CHARS", 5_000_000)
    monkeypatch.setattr(extract, "MAX_PDF_PAGES", 2)
    with pytest.raises(FileTooLarge):
        extract_text(make_pdf(), "application/pdf")


def test_unparseable_carries_user_message() -> None:
    with pytest.raises(UnparseableFile) as err:
        extract_text(b"%PDF-1.4 nope", "application/pdf")
    assert err.value.user_message == UNPARSEABLE_ERROR
