"""Knowledge: per-agent documents, background ingestion, search tool and citations (SPEC §10.1, §14.2, D1).

Public interface (the only names other packages may use):
"""

from topskill.domain import Citation, CitationCollector, Passage
from topskill.knowledge.citations import Collector, new_collector
from topskill.knowledge.service import delete_file, ensure_store, has_ready_files, ingest_file, search
from topskill.knowledge.tool import make_search_tool

__all__ = [
    "Citation",
    "CitationCollector",
    "Collector",
    "Passage",
    "delete_file",
    "ensure_store",
    "has_ready_files",
    "ingest_file",
    "make_search_tool",
    "new_collector",
    "search",
]
