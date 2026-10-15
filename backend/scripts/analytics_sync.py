"""Run the Firestore → BigQuery analytics sync once from a laptop (SPEC §18 analytics).

Usage (from backend/, settings from the environment / backend/.env):
    uv run python -m scripts.analytics_sync [--full] [--dataset topskill_analytics_dev] [--memory-state]

``--memory-state`` keeps the watermarks in memory (nothing written to Firestore; implies a full copy).
Against prod Firestore from a laptop, export ``grpc_proxy`` first (SPEC §4.1 gotchas).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


async def _run(full: bool, dataset: str | None, memory_state: bool) -> int:
    from topskill.analytics.sync import FirestoreSyncState, MemorySyncState, SyncJob
    from topskill.analytics.warehouse import BigQueryWarehouse
    from topskill.config import get_settings

    s = get_settings()
    ds = dataset or s.analytics_dataset
    if s.env != "prod" and ds == "topskill_analytics" and s.firestore_emulator_host:
        print("refusing to copy emulator data into the prod dataset", file=sys.stderr)
        return 2
    wh = BigQueryWarehouse(
        project=s.analytics_project or s.google_cloud_project, dataset=ds, location=s.analytics_location
    )
    state = MemorySyncState() if memory_state else FirestoreSyncState()
    report = await SyncJob(wh, state).run(full=full or memory_state)
    print(json.dumps(report.to_wire(), indent=2))
    return 0 if report.ran else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="ignore watermarks (backfill)")
    parser.add_argument("--dataset", help="override ANALYTICS_DATASET")
    parser.add_argument("--memory-state", action="store_true", help="do not read/write sync state")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.full, args.dataset, args.memory_state))


if __name__ == "__main__":
    raise SystemExit(main())
