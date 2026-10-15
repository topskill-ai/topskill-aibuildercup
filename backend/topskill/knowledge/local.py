"""``local`` knowledge backend: in-Firestore chunks + a term-frequency scorer (SPEC §5.1, §10.1).

Used in dev/test and as the production fallback. Chunks live in
``agents/{agent_id}/knowledge_chunks/{file_id}_{i:04d}`` with a ``terms`` count map. Search scores every
chunk of the agent in memory: ``sum(log(1 + count))`` over the distinct query terms, ties broken by the
earlier chunk; when nothing scores, a case-insensitive substring match is used instead. Not BM25 on
purpose – it stays small and predictable.
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass

from google.cloud.firestore_v1.base_query import FieldFilter

from topskill.db import Col, get_db
from topskill.domain import Passage
from topskill.knowledge.base import BackendName, ExtractedText, IngestResult, KnowledgeFileDoc
from topskill.knowledge.extract import split_sections

LOCAL_STORE = "local"
CHUNK_CHARS = 1200
MAX_TERMS_PER_CHUNK = 300
CACHE_TTL_S = 60.0
_BATCH = 400

_WORD_RE = re.compile(r"[^\W_]+")
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]")

STOPWORDS = frozenset(
    """a about above after again against all am an and any are as at be because been before being below
    between both but by can could did do does doing down during each few for from further had has have
    having he her here hers herself him himself his how i if in into is it its itself just me more most my
    myself no nor not now of off on once only or other our ours ourselves out over own same she should so
    some such than that the their theirs them themselves then there these they this those through to too
    under until up very was we were what when where which while who whom why will with would you your
    yours yourself yourselves please tell me know want need get""".split()
)


def tokenize(text: str) -> list[str]:
    """Lower-cased word terms minus stopwords; CJK runs become character bigrams."""
    terms: list[str] = []
    for match in _WORD_RE.finditer(text.lower()):
        word = match.group(0)
        if _CJK_RE.search(word):
            if len(word) == 1:
                terms.append(word)
            else:
                terms.extend(word[i : i + 2] for i in range(len(word) - 1))
        elif len(word) >= 2 and word not in STOPWORDS:
            terms.append(word)
    return terms


def term_counts(text: str) -> dict[str, int]:
    """The ``terms`` map of a chunk: at most ``MAX_TERMS_PER_CHUNK`` most frequent terms."""
    counts = Counter(tokenize(text))
    return dict(counts.most_common(MAX_TERMS_PER_CHUNK))


@dataclass(frozen=True)
class Chunk:
    file_id: str
    filename: str
    i: int
    text: str
    page_start: int | None
    page_end: int | None


def make_chunks(file: KnowledgeFileDoc, text: ExtractedText) -> list[dict]:
    """Chunk documents (≤ ``CHUNK_CHARS`` chars) keeping page numbers when they are real pages."""
    docs: list[dict] = []
    for page_no, page in enumerate(text.pages, start=1):
        for section in split_sections(page, CHUNK_CHARS):
            page_value = page_no if text.real_pages else None
            docs.append(
                {
                    "file_id": file.file_id,
                    "filename": file.filename,
                    "i": len(docs),
                    "text": section,
                    "page_start": page_value,
                    "page_end": page_value,
                    "terms": term_counts(section),
                }
            )
    return docs


def score_chunks(chunks: list[Chunk], terms_of: list[dict[str, int]], query: str) -> list[tuple[float, int]]:
    """``(score, index)`` for every chunk that matches, best first; ties keep the earlier chunk."""
    query_terms = list(dict.fromkeys(tokenize(query)))
    scored: list[tuple[float, int]] = []
    if query_terms:
        for idx, terms in enumerate(terms_of):
            score = sum(math.log1p(terms.get(t, 0)) for t in query_terms)
            if score > 0:
                scored.append((score, idx))
    if not scored:  # substring fallback (stopwords only, partial words, odd scripts)
        needle = query.strip().lower()
        raw = [w for w in dict.fromkeys(_WORD_RE.findall(needle)) if len(w) >= 2]
        for idx, chunk in enumerate(chunks):
            hay = chunk.text.lower()
            score = 2.0 if needle and needle in hay else float(sum(1 for w in raw if w in hay))
            if score > 0:
                scored.append((score, idx))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return scored


@dataclass
class _CacheEntry:
    rev: int
    expires: float
    chunks: list[Chunk]
    terms: list[dict[str, int]]


class LocalBackend:
    name: BackendName = "local"

    def __init__(self) -> None:
        self._cache: dict[str, _CacheEntry] = {}

    def owns(self, store: str | None) -> bool:
        return store == LOCAL_STORE

    async def create_store(self, agent_id: str) -> str:
        return LOCAL_STORE

    async def delete_store(self, store: str) -> None:
        return None

    def _chunks_col(self, agent_id: str):
        return get_db().collection(Col.AGENTS).document(agent_id).collection(Col.KNOWLEDGE_CHUNKS)

    async def ingest(
        self, agent_id: str, store: str, file: KnowledgeFileDoc, data: bytes, text: ExtractedText
    ) -> IngestResult:
        docs = make_chunks(file, text)
        col = self._chunks_col(agent_id)
        db = get_db()
        for start in range(0, len(docs), _BATCH):
            batch = db.batch()
            for doc in docs[start : start + _BATCH]:
                batch.set(col.document(f"{file.file_id}_{doc['i']:04d}"), doc)
            await batch.commit()
        self._cache.pop(agent_id, None)
        return IngestResult(pages=text.page_count, chunks=len(docs))

    async def delete(self, agent_id: str, store: str | None, file: KnowledgeFileDoc) -> None:
        col = self._chunks_col(agent_id)
        db = get_db()
        refs = [
            snap.reference
            async for snap in col.where(filter=FieldFilter("file_id", "==", file.file_id)).stream()
        ]
        for start in range(0, len(refs), _BATCH):
            batch = db.batch()
            for ref in refs[start : start + _BATCH]:
                batch.delete(ref)
            await batch.commit()
        self._cache.pop(agent_id, None)

    async def _load(self, agent_id: str, rev: int) -> _CacheEntry:
        entry = self._cache.get(agent_id)
        now = time.monotonic()
        if entry is not None and entry.rev == rev and entry.expires > now:
            return entry
        rows: list[tuple[str, int, Chunk, dict[str, int]]] = []
        async for snap in self._chunks_col(agent_id).stream():
            d = snap.to_dict() or {}
            chunk = Chunk(
                file_id=d.get("file_id", ""),
                filename=d.get("filename", ""),
                i=int(d.get("i", 0)),
                text=d.get("text", ""),
                page_start=d.get("page_start"),
                page_end=d.get("page_end"),
            )
            rows.append((chunk.file_id, chunk.i, chunk, dict(d.get("terms") or {})))
        rows.sort(key=lambda r: (r[0], r[1]))
        entry = _CacheEntry(
            rev=rev, expires=now + CACHE_TTL_S, chunks=[r[2] for r in rows], terms=[r[3] for r in rows]
        )
        self._cache[agent_id] = entry
        return entry

    async def search(self, agent_id: str, store: str, query: str, top_k: int, *, rev: int) -> list[Passage]:
        entry = await self._load(agent_id, rev)
        results: list[Passage] = []
        for score, idx in score_chunks(entry.chunks, entry.terms, query)[: max(top_k, 0)]:
            chunk = entry.chunks[idx]
            results.append(
                Passage(
                    file_id=chunk.file_id,
                    filename=chunk.filename,
                    text=chunk.text,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    score=round(score, 4),
                )
            )
        return results
