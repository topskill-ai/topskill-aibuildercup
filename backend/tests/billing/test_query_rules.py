"""Static guards for Firestore query shapes the emulator cannot check (§3.1 / §5.3)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from topskill.billing import dashboard

pytestmark = pytest.mark.unit

AGGREGATIONS = {"sum", "count", "avg"}


def _aggregation_calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        f"{path.name}:{node.lineno} .{node.func.attr}("
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in AGGREGATIONS
    ]


def test_billing_never_uses_firestore_aggregations() -> None:
    """A SUM/COUNT/AVG aggregation needs the aggregated field in a composite index that §5.3 does not
    have: prod answers 400 FAILED_PRECONDITION while the emulator happily accepts it. Billing sums
    in memory instead (dashboard income), so any aggregation call here is a regression."""
    package = Path(dashboard.__file__).parent
    offenders = [hit for path in sorted(package.glob("*.py")) for hit in _aggregation_calls(path)]
    assert offenders == []
