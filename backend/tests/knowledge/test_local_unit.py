from __future__ import annotations

from datetime import UTC, datetime

import pytest

from topskill.knowledge.base import ExtractedText, KnowledgeFileDoc
from topskill.knowledge.local import Chunk, make_chunks, score_chunks, term_counts, tokenize

pytestmark = pytest.mark.unit


def _file() -> KnowledgeFileDoc:
    now = datetime.now(UTC)
    return KnowledgeFileDoc(
        file_id="kf_" + "a" * 20,
        filename="guide.pdf",
        mime="application/pdf",
        size=1,
        gcs_path="knowledge/x/kf/guide.pdf",
        created_at=now,
        updated_at=now,
    )


def test_tokenize_drops_stopwords_and_handles_cjk() -> None:
    assert tokenize("What is the Priority-service FEE in 2026?") == ["priority", "service", "fee", "2026"]
    assert tokenize("签证费用") == ["签证", "证费", "费用"]
    assert tokenize("__init__ x_y") == ["init"]  # underscores never form (reserved) map keys


def test_term_counts_capped() -> None:
    text = " ".join(f"term{i} " * (i % 3 + 1) for i in range(400))
    counts = term_counts(text)
    assert len(counts) == 300 and all(not k.startswith("__") for k in counts)


def test_chunks_keep_pages_only_for_real_pages() -> None:
    pages = ["page one " * 10, "page two " * 300]
    pdf_chunks = make_chunks(_file(), ExtractedText(pages=pages, real_pages=True))
    assert pdf_chunks[0]["page_start"] == pdf_chunks[0]["page_end"] == 1
    assert {c["page_start"] for c in pdf_chunks[1:]} == {2} and len(pdf_chunks) >= 3
    assert [c["i"] for c in pdf_chunks] == list(range(len(pdf_chunks)))
    assert all(len(c["text"]) <= 1200 for c in pdf_chunks)
    txt_chunks = make_chunks(_file(), ExtractedText(pages=["plain text"], real_pages=False))
    assert txt_chunks[0]["page_start"] is None and txt_chunks[0]["terms"] == {"plain": 1, "text": 1}


def _chunks(texts: list[str]) -> tuple[list[Chunk], list[dict[str, int]]]:
    chunks = [Chunk("kf_x", "f.pdf", i, t, i + 1, i + 1) for i, t in enumerate(texts)]
    return chunks, [term_counts(t) for t in texts]


def test_term_frequency_ranking_and_ties() -> None:
    chunks, terms = _chunks(
        [
            "Refund policy: refund within 45 days. Refund form RF-9.",
            "Processing takes 19 days. Priority costs 480.",
            "Refund once.",
            "Refund once.",
        ]
    )
    ranked = score_chunks(chunks, terms, "how do I get a refund?")
    assert [idx for _, idx in ranked] == [0, 2, 3]  # most refunds first; tie keeps the earlier chunk
    assert score_chunks(chunks, terms, "priority processing")[0][1] == 1


def test_substring_fallback_when_nothing_scores() -> None:
    chunks, terms = _chunks(["Form RF-9 is required.", "Nothing here."])
    assert score_chunks(chunks, terms, "zzz") == []
    assert score_chunks(chunks, terms, "the") == []  # stopword only and not a substring anywhere
    assert [idx for _, idx in score_chunks(chunks, terms, "requir")] == [0]  # partial word → substring
    assert [idx for _, idx in score_chunks(chunks, terms, "RF")] == [0]
