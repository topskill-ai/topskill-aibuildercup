from __future__ import annotations

import pytest

from tests.knowledge.docs import make_docx, make_pdf
from topskill.errors import ApiError
from topskill.knowledge.base import clean_filename, display_name, file_type, parse_display_name
from topskill.knowledge.extract import UnparseableFile, extract_text, split_sections

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
