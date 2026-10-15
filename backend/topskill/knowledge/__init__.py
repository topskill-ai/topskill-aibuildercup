"""Knowledge: per-agent documents, background ingestion, search tool and citations (SPEC §10.1, §14.2, D1).

Other packages use only the names below. The §14.2 contract is ``ensure_store``, ``has_ready_files``,
``search``, ``ingest_file``, ``delete_file``, ``Collector``, ``new_collector`` and ``make_search_tool``
(plus the re-exported domain types). ``add_files`` / ``Upload`` (seed script), ``counts`` (the
``Agent.knowledge`` summary) and ``sweep_stale_all`` (startup sweep) are additive helpers.
"""

from topskill.domain import Citation, CitationCollector, Passage
from topskill.knowledge.citations import Collector, new_collector
from topskill.knowledge.service import (
    Upload,
    add_files,
    counts,
    delete_file,
    ensure_store,
    has_ready_files,
    ingest_file,
    search,
    sweep_stale_all,
)
from topskill.knowledge.tool import make_search_tool

__all__ = [
    "Citation",
    "CitationCollector",
    "Collector",
    "Passage",
    "Upload",
    "add_files",
    "counts",
    "delete_file",
    "ensure_store",
    "has_ready_files",
    "ingest_file",
    "make_search_tool",
    "new_collector",
    "search",
    "sweep_stale_all",
]
