"""Creator analytics: "Ask your data" over BigQuery (P2, SPEC §18).

Firestore stays the source of truth; :mod:`.sync` copies it incrementally into the BigQuery dataset
``topskill_analytics``; :mod:`.nlq` turns a creator's question into one guarded, owner-filtered SELECT.
No other package imports this one; ``core/main.py`` mounts ``router``.
"""

from topskill.analytics.nlq import SUGGESTED_QUESTIONS, AskResult
from topskill.analytics.service import Analytics, analytics_available, get_analytics
from topskill.analytics.sql_guard import SqlRejected, check_sql, wrap_sql
from topskill.analytics.sync import SyncJob, SyncReport

__all__ = [
    "SUGGESTED_QUESTIONS",
    "Analytics",
    "AskResult",
    "SqlRejected",
    "SyncJob",
    "SyncReport",
    "analytics_available",
    "check_sql",
    "get_analytics",
    "wrap_sql",
]
