"""Text extraction per page (SPEC §10.1).

* PDF → one entry per physical page (pypdf); page numbers are real and shown in citations.
* DOCX → paragraphs, split into ~1200-char sections.
* Markdown / text / CSV → decoded text, split into ~1200-char sections on line boundaries.

Extraction is CPU-bound and synchronous; callers run :func:`extract_text` in a thread.

Resource limits (a 10 MB upload must never take the single instance down):

* DOCX is a zip: before python-docx touches it, the archive is checked – at most ``MAX_ZIP_ENTRIES``
  members, at most ``MAX_DOCX_UNCOMPRESSED`` bytes in total, no large member compressed more than
  ``MAX_ZIP_RATIO``:1 – and ``word/document.xml`` is read with a bounded stream (a header that understates
  the size fails the CRC check instead of allocating). Violations raise :class:`UnparseableFile`, or
  :class:`FileTooLarge` when the archive is simply too big.
* PDF: at most ``MAX_PDF_PAGES`` pages.
* Every format: at most ``MAX_EXTRACTED_CHARS`` characters of text (:class:`FileTooLarge`).
"""

from __future__ import annotations

import io
import logging
import re
import zipfile

from topskill.knowledge.base import TOO_LARGE_ERROR, UNPARSEABLE_ERROR, ExtractedText

log = logging.getLogger(__name__)

SECTION_CHARS = 1200
MAX_EXTRACTED_CHARS = 5_000_000
MAX_PDF_PAGES = 2000
MAX_ZIP_ENTRIES = 200
MAX_DOCX_UNCOMPRESSED = 50 * 1024 * 1024
MAX_DOCX_PART = 25 * 1024 * 1024
MAX_ZIP_RATIO = 100
RATIO_CHECK_MIN_BYTES = 1024 * 1024  # small parts may compress very well; only large ones matter
DOCX_MAIN_PART = "word/document.xml"
_WS_RE = re.compile(r"[ \t\f\v ]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


class UnparseableFile(Exception):
    """The bytes are not a valid document of the declared type."""

    user_message = UNPARSEABLE_ERROR


class FileTooLarge(UnparseableFile):
    """The document is valid but would expand beyond what we index (pages, characters, unzipped size)."""

    user_message = TOO_LARGE_ERROR


def _check_chars(total: int) -> None:
    if total > MAX_EXTRACTED_CHARS:
        raise FileTooLarge(f"more than {MAX_EXTRACTED_CHARS} characters of text")


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = "\n".join(_WS_RE.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


def split_sections(text: str, size: int = SECTION_CHARS) -> list[str]:
    """Split text into sections of at most ``size`` chars, preferring line then word boundaries."""
    text = text.strip()
    if not text:
        return []
    sections: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > size:  # a single very long line: cut at a space when possible
            cut = line.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            head, line = line[:cut].rstrip(), line[cut:].lstrip()
            if current:
                sections.append(current)
                current = ""
            sections.append(head)
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > size:
            sections.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        sections.append(current)
    return [s.strip() for s in sections if s.strip()]


def _decode(data: bytes) -> str:
    encoding = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _pdf_pages(data: bytes) -> list[str]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise UnparseableFile("encrypted pdf") from exc
        if len(reader.pages) > MAX_PDF_PAGES:
            raise FileTooLarge(f"more than {MAX_PDF_PAGES} pages")
        pages: list[str] = []
        total = 0
        for page in reader.pages:
            try:
                text = _tidy(page.extract_text() or "")
            except Exception:  # one bad page must not lose the others
                log.warning("pdf_page_extract_failed", exc_info=True)
                text = ""
            total += len(text)
            _check_chars(total)
            pages.append(text)
        return pages
    except UnparseableFile:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError, RecursionError) as exc:
        raise UnparseableFile(f"pdf: {type(exc).__name__}") from exc


def _check_docx_archive(data: bytes) -> None:
    """Reject zip bombs before python-docx inflates every part into memory."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise UnparseableFile(f"docx: {len(infos)} zip entries")
            total = 0
            for info in infos:
                total += info.file_size
                if info.file_size > MAX_DOCX_PART:
                    raise FileTooLarge(f"docx: part {info.filename!r} is {info.file_size} bytes")
                if info.file_size > RATIO_CHECK_MIN_BYTES and info.file_size > MAX_ZIP_RATIO * max(
                    info.compress_size, 1
                ):
                    raise UnparseableFile(f"docx: part {info.filename!r} compressed beyond {MAX_ZIP_RATIO}:1")
            if total > MAX_DOCX_UNCOMPRESSED:
                raise FileTooLarge(f"docx: {total} bytes uncompressed")
            # Headers can lie: inflate the main part through a bounded stream. zipfile stops at the
            # declared size and then fails the CRC check, so a lie raises instead of allocating.
            if DOCX_MAIN_PART in zf.namelist():
                with zf.open(DOCX_MAIN_PART) as part:
                    if len(part.read(MAX_DOCX_PART + 1)) > MAX_DOCX_PART:
                        raise FileTooLarge("docx: main part too large")
    except UnparseableFile:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, KeyError, ValueError, OSError, EOFError) as exc:
        raise UnparseableFile(f"docx: {type(exc).__name__}") from exc


def _docx_text(data: bytes) -> str:
    import docx
    from docx.opc.exceptions import PackageNotFoundError

    _check_docx_archive(data)
    try:
        document = docx.Document(io.BytesIO(data))
    except (PackageNotFoundError, ValueError, KeyError, OSError) as exc:
        raise UnparseableFile(f"docx: {type(exc).__name__}") from exc
    except Exception as exc:  # zipfile.BadZipFile and lxml errors
        raise UnparseableFile(f"docx: {type(exc).__name__}") from exc
    parts: list[str] = []
    total = 0
    for p in document.paragraphs:
        parts.append(p.text)
        total += len(p.text)
        _check_chars(total)
    for table in document.tables:
        for row in table.rows:
            line = " | ".join(cell.text.strip() for cell in row.cells)
            parts.append(line)
            total += len(line)
            _check_chars(total)
    return _tidy("\n".join(parts))


def extract_text(data: bytes, mime: str) -> ExtractedText:
    """Pages of text for an accepted upload. Raises :class:`UnparseableFile` for corrupt files."""
    if mime == "application/pdf":
        return ExtractedText(pages=_pdf_pages(data), real_pages=True)
    if mime.endswith("wordprocessingml.document"):
        return ExtractedText(pages=split_sections(_docx_text(data)), real_pages=False)
    text = _tidy(_decode(data))
    _check_chars(len(text))
    return ExtractedText(pages=split_sections(text), real_pages=False)
