"""Wiring of the analytics feature: settings → warehouse, sync state, planner (SPEC §18.4).

:func:`get_analytics` is the FastAPI dependency; tests override it with a fake warehouse/planner.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

from topskill import tasks
from topskill.analytics.nlq import AskLimits, AskResult, Planner, ask
from topskill.analytics.sync import FirestoreSyncState, SyncJob, SyncReport, SyncState, sync_if_stale
from topskill.analytics.warehouse import BigQueryWarehouse, Warehouse
from topskill.config import Settings, get_settings

log = logging.getLogger(__name__)

PROD_DATASET = "topskill_analytics"


def analytics_available(settings: Settings) -> bool:
    """Feature flag. Outside prod the prod dataset is never written (dev data must not pollute it)."""
    if not settings.analytics_enabled:
        return False
    if not settings.is_prod and settings.analytics_dataset == PROD_DATASET:
        log.warning("analytics disabled: ENV=%s must not use the prod dataset %s", settings.env, PROD_DATASET)
        return False
    return True


@dataclass
class Analytics:
    warehouse: Warehouse
    state: SyncState
    planner: Planner
    limits: AskLimits
    enabled: bool
    sync_min_interval_s: float
    sync_wait_s: float

    def job(self) -> SyncJob:
        return SyncJob(self.warehouse, self.state)

    async def sync(self, *, full: bool = False) -> SyncReport:
        return await self.job().run(full=full)

    async def refresh_for_question(self) -> SyncReport:
        """Throttled on-demand sync before a question; never raises (fail-open on stale data)."""
        try:
            return await sync_if_stale(
                self.job,
                min_interval_s=self.sync_min_interval_s,
                wait_s=self.sync_wait_s,
                spawn=lambda coro: tasks.spawn(coro, name="analytics:sync"),  # type: ignore[arg-type]
            )
        except Exception as exc:
            log.warning("analytics_on_demand_sync_failed error=%s", str(exc)[:300])
            return SyncReport(ran=False, reason="failed")

    async def ask(self, question: str, uid: str) -> AskResult:
        return await ask(question, uid, warehouse=self.warehouse, planner=self.planner, limits=self.limits)

    async def data_as_of(self) -> datetime | None:
        try:
            return await self.state.last_finished_at()
        except Exception as exc:
            log.warning("analytics_state_unavailable error=%s", type(exc).__name__)
            return None


@lru_cache(maxsize=1)
def _default() -> Analytics:
    s = get_settings()
    chain: list[str] = []
    for model in (s.model_analytics, s.model_analytics_fallback):
        if model and model not in chain:
            chain.append(model)
    return Analytics(
        warehouse=BigQueryWarehouse(
            project=s.analytics_project or s.google_cloud_project,
            dataset=s.analytics_dataset,
            location=s.analytics_location,
        ),
        state=FirestoreSyncState(),
        planner=Planner(chain),
        limits=AskLimits(
            max_bytes=s.analytics_max_bytes,
            timeout_s=s.analytics_query_timeout_s,
            max_rows=s.analytics_max_rows,
            plan_timeout_s=s.analytics_plan_timeout_s,
            answer_timeout_s=s.analytics_answer_timeout_s,
            ask_timeout_s=s.analytics_ask_timeout_s,
        ),
        enabled=analytics_available(s),
        sync_min_interval_s=s.analytics_sync_min_interval_s,
        sync_wait_s=s.analytics_sync_wait_s,
    )


def get_analytics() -> Analytics:
    """FastAPI dependency (module singleton built from settings)."""
    return _default()
