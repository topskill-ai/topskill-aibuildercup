from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest

from tests.a2a.fakes import FakeBilling, FakeDeps, RuntimeFactory
from tests.conftest import dev_headers
from topskill.a2a import _deps, executor


@dataclass
class A2AEnv:
    billing: FakeBilling
    runtime: RuntimeFactory


@pytest.fixture
def a2a_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[A2AEnv]:
    """Billing double + scripted runtime agents behind the a2a lane's dependency seam."""
    env = A2AEnv(billing=FakeBilling(), runtime=RuntimeFactory())
    monkeypatch.setattr(_deps, "get_deps", lambda: FakeDeps(billing=env.billing))
    monkeypatch.setattr(_deps, "build_runtime_agent", env.runtime)
    executor.clear_stacks()
    yield env
    executor.clear_stacks()


async def issue_mandate(
    client: httpx.AsyncClient,
    uid: str,
    *,
    audience: str = "*",
    max_amount_cents: int = 500,
    budget_cents: int = 2000,
    ttl_minutes: int = 30,
    intent: str = "Research visa options for a new hire",
) -> tuple[dict, str]:
    resp = await client.post(
        "/api/mandates",
        json={
            "audience": audience,
            "intent": intent,
            "max_amount_cents": max_amount_cents,
            "budget_cents": budget_cents,
            "ttl_minutes": ttl_minutes,
            "payer_agent": "enterprise-assistant",
        },
        headers=dev_headers(uid),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["mandate"], body["token"]


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for frame in text.split("\n\n"):
        name, data = None, []
        for line in frame.splitlines():
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if name:
            events.append((name, json.loads("\n".join(data))))
    return events
