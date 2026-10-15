"""Live smoke test of the Enterprise Assistant on Vertex AI (real ``FallbackModel`` over the ``assistant``
chain with ``portable_thinking_config``). The hired agents are scripted, so only the assistant's model is
live. Run manually with the emulators up: ``uv run pytest -m live tests/a2a/test_assistant_live.py``."""

from __future__ import annotations

import httpx
import pytest

from tests.a2a.conftest import A2AEnv, parse_sse
from tests.a2a.test_assistant import TASK, _agents, _payer
from tests.conftest import dev_headers
from tests.core.helpers import settings_env
from topskill.a2a import assistant

pytestmark = [pytest.mark.live, pytest.mark.emulator]


async def test_live_assistant_hires_agents(
    app, client, a2a_env: A2AEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        assistant,
        "http_client",
        lambda timeout: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000", timeout=timeout
        ),
    )
    _, _visa, _resume, contract = await _agents("livesmoke")
    payer, _mandate, token = await _payer(client)
    with settings_env(monkeypatch, llm_mode="live"):
        resp = await client.post(
            "/api/demo/assistant/chat",
            json={"messages": [{"role": "user", "text": TASK}], "mandate_token": token},
            headers=dev_headers(payer),
            timeout=180,
        )
    assert resp.status_code == 200, resp.text
    events = parse_sse(resp.text)
    for name, data in events:
        if name != "text_delta":
            print(name, str(data)[:300])
    print("ANSWER:", "".join(d["delta"] for n, d in events if n == "text_delta"))
    names = [n for n, _ in events]
    assert names[0] == "status" and names[-1] == "done" and names.count("done") == 1
    assert "a2a_discover" in names
    results = [d for n, d in events if n == "a2a_result"]
    assert any(r["status"] == "completed" and r["charged_cents"] > 0 for r in results), results
    for r in results:
        if r["slug"] == contract.slug:
            assert (
                r["status"] == "rejected"
                and r["error_code"] == "mandate_exceeds_max"
                and r["charged_cents"] == 0
            )
    done = events[-1][1]
    assert done["outcome"] == "ok", done
    assert done["mandate"]["spent_cents"] == sum(r["charged_cents"] for r in results)
