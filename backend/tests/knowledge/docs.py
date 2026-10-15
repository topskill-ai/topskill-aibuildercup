"""Small documents generated on the fly for knowledge tests (no binary fixtures)."""

from __future__ import annotations

import io

VISA_PAGES: list[tuple[str, list[str]]] = [
    (
        "Acme Visa Guide - Chapter 1: Eligibility",
        [
            "Applicants for the Acme Student Visa must be at least 17 years old.",
            "Proof of funds: a bank balance of at least 21,500 Zorkmids held for 28 consecutive days.",
            "Language requirement: IELTS 6.5 overall with no band below 6.0.",
        ],
    ),
    (
        "Chapter 2: Processing Times",
        [
            "Standard processing takes 19 working days from the biometrics appointment.",
            "Priority service costs 480 Zorkmids and reduces processing to 5 working days.",
            "Super priority is only available in Lumeria City and takes 1 working day.",
        ],
    ),
    (
        "Chapter 3: Refund Policy",
        [
            "If an application is withdrawn before biometrics, 70 percent of the fee is refunded.",
            "No refund is issued after a decision has been made.",
            "Refund requests must be filed within 45 days using form RF-9.",
        ],
    ),
]


def make_pdf(pages: list[tuple[str, list[str]]] | None = None, *, blank_pages: int = 0) -> bytes:
    """A text PDF (one page per entry); ``blank_pages`` adds pages without any text."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for title, lines in pages if pages is not None else VISA_PAGES:
        c.setFont("Helvetica-Bold", 16)
        c.drawString(72, 780, title)
        c.setFont("Helvetica", 12)
        for i, line in enumerate(lines):
            c.drawString(72, 740 - 24 * i, line)
        c.showPage()
    for _ in range(blank_pages):
        c.rect(72, 72, 100, 100)
        c.showPage()
    c.save()
    return buf.getvalue()


def make_docx(paragraphs: list[str]) -> bytes:
    import docx

    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()
