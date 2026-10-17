#!/usr/bin/env bash
# Production smoke test (SPEC 5.3 / 12.3): hit every list endpoint with every filter combination
# once and fail on any 5xx (a missing composite index surfaces as a 500 FAILED_PRECONDITION).
#
#   deploy/scripts/smoke_prod.sh                         # public endpoints only
#   SMOKE_EMAIL=... SMOKE_PASSWORD=... deploy/scripts/smoke_prod.sh   # + signed-in endpoints
#   SMOKE_ID_TOKEN=... deploy/scripts/smoke_prod.sh      # same, with a ready Firebase ID token
#   deploy/scripts/smoke_prod.sh --knowledge             # + one knowledge search (File Search via the
#                                                        #   runtime's GEMINI_API_KEY) on an agent with
#                                                        #   ready files (SMOKE_KNOWLEDGE_AGENT_ID or auto)
#   deploy/scripts/smoke_prod.sh --base http://127.0.0.1:8000 --dev-user smoke-creator
#                                                        # local stack with the dev auth bypass
#   deploy/scripts/smoke_prod.sh --allow-missing         # walking skeleton: a 404 from a domain
#                                                        #   router that is not merged yet is "absent"
#
# The signed-in account should be a creator (it is upgraded with POST /api/me/become-creator if
# not). Credentials are read from the environment only and never printed. Exit code 1 on failure.
set -euo pipefail
exec python3 - "$@" <<'PY'
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROD_API = "https://topskill-api-920441209758.asia-southeast1.run.app"
WEB_API_KEY = os.environ.get("FIREBASE_WEB_API_KEY", "AIzaSyBINjnUKFWfLiSPREGBXUFx0xX8Z9-BHA0")

ap = argparse.ArgumentParser(prog="smoke_prod.sh")
ap.add_argument("--base", default=os.environ.get("SMOKE_API_BASE", PROD_API))
ap.add_argument("--dev-user", default=None, help="use X-Dev-User auth (local stack only)")
ap.add_argument("--knowledge", action="store_true", help="also run one knowledge search")
ap.add_argument("--max-agents", type=int, default=3)
ap.add_argument("--allow-missing", action="store_true",
                help="treat 404 as 'absent' (domain routers not merged yet); 5xx still fails")
args = ap.parse_args(sys.argv[1:])
BASE = args.base.rstrip("/")
is_local = urllib.parse.urlparse(BASE).hostname in {"127.0.0.1", "localhost"}
opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if is_local else urllib.request.build_opener()

results: list[tuple[str, str, int, str]] = []
failures: list[str] = []
headers: dict[str, str] = {}


def call(method: str, path: str, *, body=None, query=None, auth=False, ok_404=False, must=True, label=None):
    """One request. Fails on 5xx / no response / 401 with credentials; with must=True (MUST endpoints,
    SPEC 6.4) also on any other non-2xx, except a 404 when ok_404 (SHOULD endpoint not built)."""
    ok_404 = ok_404 or args.allow_missing
    qs = urllib.parse.urlencode({k: v for k, v in (query or {}).items() if v is not None})
    url = BASE + path + (f"?{qs}" if qs else "")
    data = json.dumps(body).encode() if body is not None else None
    h = {"Accept": "application/json"}
    if data is not None:
        h["Content-Type"] = "application/json"
    if auth:
        h.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    t0 = time.monotonic()
    status, payload, note = 0, None, ""
    try:
        with opener.open(req, timeout=60) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read()
    except Exception as e:  # network error, timeout
        raw = b""
        note = f"{type(e).__name__}: {e}"
    ms = int((time.monotonic() - t0) * 1000)
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = None
    if isinstance(payload, dict) and status >= 400:
        note = f"{payload.get('code')} trace={payload.get('trace_id')}"
    name = label or f"{method} {path}" + (f"?{qs}" if qs else "")
    if status == 0 or status >= 500:
        failures.append(f"{name}: {status or 'no response'} {note}")
        verdict = "FAIL"
    elif auth and status == 401:
        failures.append(f"{name}: 401 with credentials {note}")
        verdict = "FAIL"
    elif status == 404 and ok_404:
        verdict = "absent"
    elif status >= 400 and must:
        failures.append(f"{name}: {status} {note}")
        verdict = "FAIL"
    elif status >= 400:
        verdict = "warn"
    else:
        verdict = "ok"
    results.append((verdict, name, status, f"{ms} ms {note}".strip()))
    return status, payload


def items(payload) -> list:
    return payload.get("items", []) if isinstance(payload, dict) else []


def sign_in() -> bool:
    if args.dev_user:
        headers.update({"X-Dev-User": args.dev_user, "X-Dev-Role": "creator"})
        return True
    token = os.environ.get("SMOKE_ID_TOKEN")
    email, password = os.environ.get("SMOKE_EMAIL"), os.environ.get("SMOKE_PASSWORD")
    if not token and email and password:
        req = urllib.request.Request(
            "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" + WEB_API_KEY,
            data=json.dumps({"email": email, "password": password, "returnSecureToken": True}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                token = json.load(resp)["idToken"]
        except urllib.error.HTTPError as e:
            failures.append(f"sign-in failed: HTTP {e.code}")
            return False
    if not token:
        return False
    headers["Authorization"] = f"Bearer {token}"
    return True


# ---- public -------------------------------------------------------------------------------
# Cloud Run's front end reserves URL paths ending in "z": /healthz answers a Google 404 from outside
# (the in-container startup probe still uses it). Prod therefore uses the /health alias when present.
health = None
for path in ("/healthz",) if is_local else ("/health",):
    status, body = call("GET", path, ok_404=not is_local)
    if status == 200 and isinstance(body, dict):
        health = body
        print(f"{path}: env={body.get('env')} version={body.get('version')}")
        if not is_local and body.get("env") != "prod":
            failures.append(f"{path} env is {body.get('env')!r}, expected 'prod'")
if health is None and not is_local:
    print("note: no /health alias on this build; liveness checked via /api/public/meta only")


def preflight(origin: str, allowed: bool) -> None:
    """CORS preflight as the browser sends it for an authenticated JSON call (SPEC 2, decision 2)."""
    req = urllib.request.Request(BASE + "/api/me", method="OPTIONS", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    })
    try:
        with opener.open(req, timeout=30) as resp:
            status, acao = resp.status, resp.headers.get("Access-Control-Allow-Origin")
    except urllib.error.HTTPError as e:
        status, acao = e.code, e.headers.get("Access-Control-Allow-Origin")
    except Exception as e:
        status, acao = 0, None
        failures.append(f"CORS preflight {origin}: {type(e).__name__}: {e}")
    good = (status == 200 and acao == origin) if allowed else (acao != origin and status < 500)
    if not good:
        failures.append(f"CORS preflight {origin}: HTTP {status}, allow-origin={acao!r}")
    results.append(("ok" if good else "FAIL", f"OPTIONS /api/me (Origin {origin})", status, f"allow={acao}"))


if is_local:
    preflight("http://localhost:5173", allowed=True)
else:
    preflight("https://www.openskill.top", allowed=True)
    preflight("https://top-skill.web.app", allowed=True)
    preflight("https://top-skill.firebaseapp.com", allowed=True)
    preflight("https://top-skill--smoke-0000abcd.web.app", allowed=True)
preflight("https://evil.example.com", allowed=False)
call("GET", "/api/public/meta")


def expect_401(label: str, extra: dict[str, str]) -> None:
    """Auth is enforced: no token → 401; in prod the X-Dev-* bypass headers are ignored (SPEC 4.2)."""
    req = urllib.request.Request(BASE + "/api/me", headers={"Accept": "application/json", **extra})
    try:
        with opener.open(req, timeout=30) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception:
        status = 0
    good = status == 401
    if not good:
        failures.append(f"GET /api/me ({label}): HTTP {status}, expected 401")
    results.append(("ok" if good else "FAIL", f"GET /api/me ({label})", status, "expect 401"))


expect_401("no token", {})
if not is_local:
    expect_401("X-Dev-User ignored in prod", {"X-Dev-User": "smoke-probe", "X-Dev-Role": "creator"})
status, jwks = call("GET", "/.well-known/jwks.json")
if status == 200 and not (isinstance(jwks, dict) and jwks.get("keys")):
    failures.append("jwks.json has no keys")
_, pub = call("GET", "/api/public/agents")
call("GET", "/api/public/agents", query={"q": "visa"})
_, page1 = call("GET", "/api/public/agents", query={"limit": 1})
if isinstance(page1, dict) and page1.get("next_cursor"):
    call("GET", "/api/public/agents", query={"limit": 1, "cursor": page1["next_cursor"]})
call("GET", "/api/public/a2a/directory")
call("GET", "/api/public/a2a/directory", query={"q": "visa"})
public_slugs = [a.get("slug") for a in items(pub) if a.get("slug")]
for slug in public_slugs[:1]:
    call("GET", f"/api/public/agents/{slug}")
    call("GET", f"/a2a/{slug}/.well-known/agent-card.json")

# ---- signed in ----------------------------------------------------------------------------
if sign_in():
    _, me = call("GET", "/api/me", auth=True)
    if isinstance(me, dict) and me.get("role") != "creator":
        call("POST", "/api/me/become-creator", body={}, auth=True)
    call("GET", "/api/wallet", auth=True)
    call("GET", "/api/dashboard", auth=True)
    _, agents = call("GET", "/api/agents", auth=True)
    agent_list = items(agents)
    agent_ids = [a["id"] for a in agent_list if a.get("id")][: args.max_agents]
    probe_agent = agent_ids[0] if agent_ids else "agt_00000000000000000000"

    for category in (None, "creator_income"):
        for agent_id in (None, probe_agent):
            call("GET", "/api/wallet/ledger", query={"category": category, "agent_id": agent_id}, auth=True)
    _, l1 = call("GET", "/api/wallet/ledger", query={"limit": 1}, auth=True)
    if isinstance(l1, dict) and l1.get("next_cursor"):
        call("GET", "/api/wallet/ledger", query={"limit": 1, "cursor": l1["next_cursor"]}, auth=True)

    knowledge_ready: list[str] = []
    for agent_id in agent_ids:
        call("GET", f"/api/agents/{agent_id}", auth=True)
        call("GET", f"/api/agents/{agent_id}/earnings", auth=True)
        for st in (None, "in_progress"):
            for fs in (None, "new"):
                call("GET", f"/api/agents/{agent_id}/records", query={"status": st, "follow_status": fs},
                     auth=True)
        call("GET", f"/api/agents/{agent_id}/conversations", auth=True, ok_404=True)
        _, kn = call("GET", f"/api/agents/{agent_id}/knowledge", auth=True)
        if any(f.get("status") == "ready" for f in items(kn)):
            knowledge_ready.append(agent_id)
    for a in agent_list[:1]:
        if a.get("slug"):
            call("GET", f"/api/public/agents/{a['slug']}/billing", auth=True, must=False)

    _, mandates = call("GET", "/api/mandates", auth=True, ok_404=True)
    for m in items(mandates)[:1]:
        call("GET", f"/api/mandates/{m['id']}/calls", auth=True, ok_404=True)

    if args.knowledge:
        target = os.environ.get("SMOKE_KNOWLEDGE_AGENT_ID") or (knowledge_ready[0] if knowledge_ready else None)
        if not target:
            failures.append("--knowledge: no agent with ready knowledge files")
        else:
            st, res = call("POST", f"/api/agents/{target}/knowledge/search", must=False,
                           body={"query": os.environ.get("SMOKE_KNOWLEDGE_QUERY", "work visa requirements")},
                           auth=True, label=f"POST knowledge/search ({target})")
            if st == 200 and not items(res):
                failures.append("--knowledge: search returned no passages")
            elif st != 200:
                failures.append(f"--knowledge: search returned HTTP {st}")
else:
    print("note: no credentials (SMOKE_ID_TOKEN, SMOKE_EMAIL/SMOKE_PASSWORD or --dev-user); "
          "signed-in endpoints skipped")
    if args.knowledge:
        failures.append("--knowledge needs credentials")

# ---- report -------------------------------------------------------------------------------
width = max((len(r[1]) for r in results), default=10)
for verdict, name, status, note in results:
    print(f"{verdict:6} {status:>3}  {name:<{width}}  {note}")
print(f"\n{len(results)} requests against {BASE}")
if failures:
    print("FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("smoke OK")
PY
