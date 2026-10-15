"""Generate ``knowledge/visa_guide.pdf`` (the visa-advisor demo knowledge document) and the test fixture copy.

Run from ``backend/``: ``uv run python -m scripts.demo_data.make_visa_guide``. Needs the dev dependency
``reportlab``. The output is committed; rerun only when the text below changes. Each section is one page so
that citations point at predictable page numbers (documents and lead times are on page 2).
"""

# ruff: noqa: E501  (long English prose in data literals)

from __future__ import annotations

import shutil
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

HERE = Path(__file__).resolve().parent
OUT = HERE / "knowledge" / "visa_guide.pdf"
FIXTURE = HERE.parent.parent / "tests" / "fixtures" / "visa_guide.pdf"

TITLE = "Northbridge Mobility - Work Visa Guide for International Hires (2026 edition)"

PAGES: list[tuple[str, list[str]]] = [
    (
        "1. About this guide and our service packages",
        [
            "This guide is the internal playbook of Northbridge Mobility, the immigration practice of consultant "
            "Ana Silva. It summarises how we plan work visas for international new hires. Government rules change "
            "often: always confirm current salary thresholds and fees on the official government website before "
            "filing.",
            "<b>Essential package - 450 EUR.</b> Route assessment, personalised document checklist, review of the "
            "application before submission. Suitable for single applicants with a straightforward case.",
            "<b>Relocation package - 1,200 EUR.</b> Everything in Essential plus appointment booking, translation "
            "management, family applications (spouse and children), and support until the residence card is "
            "issued.",
            "<b>Employer retainer - from 3,500 EUR per year.</b> For companies hiring five or more international "
            "employees a year: sponsor licence or employer registration, hiring-pipeline planning and a dedicated "
            "case manager.",
            "Our fees never include government fees, translations or courier costs. We refund 50 percent of the "
            "package fee if the client cancels before we start the document review, and nothing after the "
            "application has been filed.",
        ],
    ),
    (
        "2. Documents and lead times",
        [
            "Every route needs the same core set of documents. Start the slowest items on the first day.",
            "<b>Passport:</b> valid for at least 12 months after the planned start date, with two blank pages.",
            "<b>Employment contract or binding job offer:</b> must state job title, gross annual salary, working "
            "hours and start date. Unsigned drafts are not accepted by any consulate we work with.",
            "<b>Degree certificate and transcript:</b> certified copies. Germany's EU Blue Card requires a degree "
            "that is recognised (check the anabin database, or request a statement of comparability from ZAB, "
            "which takes 2 to 12 weeks).",
            "<b>Apostille:</b> civil and academic documents issued in Brazil, Mexico, India and other Hague "
            "Convention countries need an apostille. In Brazil, any notary office (cartorio) can issue it in 1 to "
            "5 working days.",
            "<b>Police clearance certificate:</b> issued less than 90 days before filing. The Brazilian Federal "
            "Police certificate is available online the same day; Indian police clearance typically takes 2 to 4 "
            "weeks.",
            "<b>Certified translations:</b> needed when a document is not in English or the destination language; "
            "allow 1 to 2 weeks.",
            "<b>Health insurance:</b> proof of cover from the first day of residence.",
            "Typical longest pole: degree recognition (up to 12 weeks) followed by the consulate appointment wait.",
        ],
    ),
    (
        "3. Routes and typical timelines we observe",
        [
            "The timelines below are what our clients experienced in 2025-2026, measured from a complete "
            "document set to visa issuance. They are not official processing times.",
            "<b>Germany - EU Blue Card:</b> for university graduates with a qualifying salary. With the employer's "
            "fast-track procedure (beschleunigtes Fachkraefteverfahren) the consulate appointment is usually "
            "offered within 3 weeks and the visa issued 2 to 4 weeks later. Without fast-track, appointment waits "
            "in Sao Paulo have been 4 to 10 weeks.",
            "<b>Netherlands - Highly skilled migrant:</b> the employer must be a recognised sponsor. Decisions "
            "usually arrive in 2 to 4 weeks; the employee can then collect the entry visa (MVV) where required.",
            "<b>Portugal - Tech visa / highly qualified activity:</b> attractive for start-ups certified under the "
            "Tech Visa programme; consulate processing has taken 6 to 12 weeks.",
            "<b>United Kingdom - Skilled Worker:</b> needs a licensed sponsor and a Certificate of Sponsorship. "
            "Decisions outside the UK usually take about 3 weeks; priority services may be available for an "
            "extra fee.",
            "Planning rule of thumb: for a Brazilian engineer joining a German employer, plan 8 to 12 weeks from "
            "signed contract to first working day with fast-track, and 12 to 16 weeks without it.",
        ],
    ),
    (
        "4. Families, pitfalls and when to refer",
        [
            "<b>Family members:</b> spouses and minor children can usually apply together with the main "
            "applicant. Marriage and birth certificates need an apostille and a certified translation. EU Blue "
            "Card family members in Germany do not need to prove language skills.",
            "<b>Common pitfalls:</b> salary below the threshold after the annual update; job title that does not "
            "match the degree; expired police certificate; unsigned contract; booking flights before the visa "
            "is issued.",
            "<b>Start dates:</b> always make the contract start date conditional on the work permit and allow a "
            "two-week buffer after the expected visa date.",
            "<b>When we refer to a lawyer:</b> previous visa refusals, criminal records, overstays, asylum cases and "
            "appeals. These need a licensed immigration lawyer and are outside our packages.",
            "<b>Contact:</b> book a call through the TopSkill agent page; we reply within one working day.",
        ],
    ),
]


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.drawString(20 * mm, 12 * mm, "Northbridge Mobility - Work Visa Guide 2026")
    canvas.drawRightString(190 * mm, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build(path: Path) -> None:
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10.5, leading=14.5, spaceAfter=7)
    heading = ParagraphStyle("h", parent=styles["Heading2"], spaceAfter=10)
    title = ParagraphStyle("t", parent=styles["Title"], fontSize=15, leading=19, spaceAfter=12)
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=TITLE,
        author="Northbridge Mobility",
    )
    story: list = []
    for i, (section, paragraphs) in enumerate(PAGES):
        if i == 0:
            story.append(Paragraph(TITLE, title))
        story.append(Paragraph(section, heading))
        story.extend(Paragraph(p, body) for p in paragraphs)
        story.append(Spacer(1, 4))
        if i < len(PAGES) - 1:
            story.append(PageBreak())
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build(OUT)
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(OUT, FIXTURE)
    print(f"wrote {OUT} and {FIXTURE}")


if __name__ == "__main__":
    main()
