"""Generate ``public/samples/career_coaching_guide.pdf``, the knowledge file of the sample skill.

The output is committed; rerun only when the text below changes. Needs ``reportlab``, which the
backend dev environment already has:

    ../backend/.venv/bin/python scripts/make_sample_guide.py   # from web/

Each section is one page so citations point at predictable page numbers.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

OUT = Path(__file__).resolve().parent.parent / "public" / "samples" / "career_coaching_guide.pdf"

TITLE = "Lena Park Career Coaching - Methods, Packages and Negotiation Playbook (2026)"

PAGES: list[tuple[str, list[str]]] = [
    (
        "1. Our coaching packages",
        [
            "This guide is the internal playbook of Lena Park Career Coaching. It describes how we work "
            "with clients and what each package includes.",
            "<b>Interview sprint - 290 EUR.</b> Two 60-minute sessions: positioning statement, the five most "
            "likely questions for one target role, and a mock interview with written feedback.",
            "<b>Job search programme - 890 EUR.</b> Six sessions over eight weeks: target-role definition, CV "
            "and LinkedIn rewrite, application tracker, interview preparation for every live process, and "
            "offer negotiation support.",
            "<b>Promotion coaching - 640 EUR.</b> Four sessions: evidence portfolio, conversation plan with the "
            "manager, and a 90-day follow-up.",
            "Clients can cancel any package before the first session for a full refund. After the first "
            "session we refund unused sessions at 50 percent.",
        ],
    ),
    (
        "2. CV and positioning method",
        [
            "A CV is read for about 30 seconds on the first pass. The top third must answer three questions: "
            "what role does this person want, what is their strongest evidence, and why now.",
            "<b>Positioning statement:</b> two sentences. Sentence one names the target role and the years of "
            "relevant experience. Sentence two names the single strongest result with a number.",
            "<b>Bullets:</b> every bullet follows action, scope, result. Example: 'Rebuilt the checkout flow "
            "for 2 million monthly users, cutting drop-off by 18 percent in one quarter.'",
            "<b>Length:</b> one page up to 8 years of experience, two pages after that. Remove anything older "
            "than 15 years unless it is directly relevant to the target role.",
            "<b>Gaps:</b> name a career break in one line (for example 'Parental leave, 2024') rather than "
            "hiding it; hiring managers react worse to unexplained gaps than to explained ones.",
        ],
    ),
    (
        "3. Negotiation playbook",
        [
            "Most offers have room for 5 to 15 percent more in total value. Negotiate once, politely, and on "
            "the whole package rather than on base salary alone.",
            "<b>Anchor on the market.</b> Before the first salary conversation, collect three data points for "
            "the role, level and city. Give a range whose bottom is your real target.",
            "<b>Never name the first number when you can avoid it.</b> Ask for the budgeted range for the role. "
            "If you must answer, use your researched range.",
            "<b>Negotiate the package.</b> Signing bonus, equity, start date, remote days, learning budget and "
            "title are often easier to move than base salary.",
            "<b>Get it in writing.</b> Do not resign from your current job until you hold the final signed "
            "offer. Verbal offers can be withdrawn.",
            "<b>Timeline:</b> ask for 3 to 5 working days to decide. Use the time to compare offers in one "
            "table: base, bonus, equity, benefits, commute and growth.",
        ],
    ),
]


def build() -> None:
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(OUT), pagesize=A4, title=TITLE, author="Lena Park Career Coaching",
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm, bottomMargin=20 * mm,
    )
    story = [Paragraph(TITLE, styles["Title"]), Spacer(1, 6 * mm)]
    for i, (heading, paragraphs) in enumerate(PAGES):
        if i > 0:
            story.append(PageBreak())
        story.append(Paragraph(heading, styles["Heading2"]))
        for text in paragraphs:
            story.append(Paragraph(text, styles["BodyText"]))
            story.append(Spacer(1, 3 * mm))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.build(story)


if __name__ == "__main__":
    build()
    print(f"wrote {OUT}")
