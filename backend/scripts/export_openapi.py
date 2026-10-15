"""Export the OpenAPI document of the API (informational; the web types follow SPEC §6.3).

Usage (from backend/): ``uv run python -m scripts.export_openapi [--out PATH]``.
Defaults to ``<repo root>/openapi.json``. Only routers whose packages exist are included.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "openapi.json")
    args = parser.parse_args(argv)

    # Building the app needs no I/O; keep logs quiet and never pick up a K_SERVICE guard here.
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    os.environ.setdefault("TRACING_ENABLED", "false")  # no exporter / ADC lookup for a schema dump
    sys.path.insert(0, str(BACKEND_DIR))
    from topskill.main import create_app

    spec = create_app().openapi()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({len(spec.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
