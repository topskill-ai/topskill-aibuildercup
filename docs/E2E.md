# TopSkill — E2E checklist

Owner: [deploy]. Source of truth for behaviour: [`SPEC.md`](SPEC.md) (§1.2 storyline, §12.3 E2E).
Every run records the date, the commit (`git rev-parse --short HEAD`), the environment (local / prod) and the screenshots listed below.

## 1. Emulators for tests: `deploy/scripts/emu_exec.sh`

Several people (and agent lanes) run tests at the same time, so nobody uses the default emulator ports for tests. `emu_exec.sh` starts private Firestore + Auth emulators for project `demo-topskill`, runs one command against them and stops them afterwards:

```bash
deploy/scripts/emu_exec.sh <FS_PORT> <AUTH_PORT> <HUB_PORT> <LOG_PORT> -- <command ...>

# e.g. from backend/
../deploy/scripts/emu_exec.sh 8102 9102 4402 4502 -- uv run pytest -m "not live"
```

What it does:
- writes a temporary config to `/private/tmp/claude-501/topskill-emu/<lane>/firebase.json` (outside macOS: `$RUNNER_TEMP` or `/tmp`), with `firestore`, `auth`, `hub`, `logging` on the given ports, the Firestore websocket on `LOG_PORT + 100`, `ui` disabled and `singleProjectMode`; `<lane>` comes from the git branch `lane/<lane>` (override with `EMU_LANE`);
- exports `FIRESTORE_EMULATOR_HOST=127.0.0.1:<FS_PORT>` and `FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:<AUTH_PORT>` (the backend `conftest.py` honours pre-set values);
- picks Java ≥ 21 (`$JAVA_HOME`, else `/opt/homebrew/opt/openjdk`);
- runs `firebase emulators:exec --config <that file> --only firestore,auth --project demo-topskill "<command>"` (the command runs in your current directory; emulator logs stay in the config directory) and returns the command's exit code.

Port plan (one row per lane; never 8080 / 9099 / 4400 / 4500 for tests):

| Lane | Firestore | Auth | Hub | Logging |
|---|---|---|---|---|
| deploy | 8102 | 9102 | 4402 | 4502 |
| others | given by the integrator per lane | | | |
| CI | 8080 | 9099 | 4400 | 4500 (fresh runner) |

## 2. Local stack: `deploy/scripts/dev_up.sh`

```bash
deploy/scripts/dev_up.sh                  # emulators + backend :8000 + web :5173
deploy/scripts/dev_up.sh --no-web         # emulators + backend
deploy/scripts/dev_up.sh --emulators-only
FS_PORT=8102 AUTH_PORT=9102 HUB_PORT=4402 LOG_PORT=4502 API_PORT=8002 WEB_PORT=5175 deploy/scripts/dev_up.sh
```

Backend runs with `ENV=dev AUTH_DEV_BYPASS=true FIRESTORE_PROJECT=demo-topskill STORAGE_BACKEND=local KNOWLEDGE_BACKEND=local LLM_MODE=live` (Gemini on Vertex via ADC). Web runs with `VITE_DEV_AUTH=1 VITE_USE_AUTH_EMULATOR=1`. To exercise File Search locally: `KNOWLEDGE_BACKEND=file_search DEV_FETCH_GEMINI_KEY=1 deploy/scripts/dev_up.sh` (the key is read from Secret Manager into the process, never printed).

## 3. E2E script (local first, then prod)

Viewports: **1440 × 900** and **375 × 812** for every screenshot marked 📷. Save as `e2e/<env>-<step>-<width>.png` (not committed; attach to the PR / submission notes).

| # | Step | Expected | 📷 |
|---|---|---|---|
| 1 | Creator signs in (local: Developer login `creator-e2e` / role creator; prod: email + password) | Studio dashboard, balance 100.00 | `studio-empty` |
| 2 | `/studio/new`: drop `backend/scripts/demo_data/visa-advisor/` + `visa_guide.pdf` | Draft fields fill in (≈ 9 s), timer running | `wizard-draft` |
| 3 | Generate avatar | Avatar appears (≈ 8 s) | `wizard-avatar` |
| 4 | Pricing step | "Visitors pay 1.00 per round, first 2 rounds free; you receive 0.80" | `wizard-pricing` |
| 5 | Publish | Redirect to agent Overview, "Published in N s" toast | `agent-overview` |
| 6 | Wait for knowledge | Overview badge "Knowledge: ready" | `knowledge-ready` |
| 7 | Visitor signs in (other account), opens `/a/<slug>` | Greeting, 3 starter questions, billing hint "2 free rounds left" | `agent-page` |
| 8 | Round 1 (starter question) | Streamed answer with `[1]` markers, Sources: `visa_guide.pdf · p. N`, web chips + search entry point, "Free round 1 of 2" | `chat-citations` |
| 9 | Round 2: state nationality / budget | Tool line "Saved N fields", "Free round 2 of 2" | `chat-record` |
| 10 | Round 3 | "Charged 1.00 · round 3", balance 99.00 | `chat-charged` |
| 11 | Creator → Records tab | New row with saved fields; Export CSV downloads (BOM, columns) | `records`, CSV |
| 12 | `/assistant`: authorise budget 20.00 / max 5.00 / 30 min | Mandate card | `assistant-mandate` |
| 13 | Ask the Brazil new-hire question | Discover card (3 agents) → `visa-advisor` charged 2.00 → `resume-reviewer` charged 2.00 → `contract-reviewer` **rejected · mandate_exceeds_max · Not charged** → final answer; budget bar 4.00 / 20.00 | `assistant-timeline` |
| 14 | Creator `/studio` | Earned today includes visitor + A2A income (separate categories), recent income rows | `dashboard-income` |
| 15 | `/wallet` (visitor and assistant user) | Ledger rows with categories, counterparty, memo | `wallet-ledger` |

Error paths to spot-check once: visitor with 0.40 credits → Paywall dialog (`visitor_insufficient_points`) with demo top-up; paused agent → "Temporarily unavailable"; Stop button mid-stream → "Not charged (stopped)".

## 4. Prod checks after every deploy

1. `deploy/scripts/deploy_backend.sh` checks `/healthz` reports the deployed sha.
2. Smoke test — every list endpoint × every filter combination of SPEC §5.3 once, fails on any 5xx (missing index) or non-2xx on a MUST endpoint:
   ```bash
   SMOKE_EMAIL=<creator account> SMOKE_PASSWORD=<from your password manager> deploy/scripts/smoke_prod.sh
   SMOKE_ID_TOKEN=<token> deploy/scripts/smoke_prod.sh --knowledge   # + one File Search call through the API
   ```
   The same script runs against a local stack with `--base http://127.0.0.1:8000 --dev-user smoke-creator`.
3. Run the E2E script above on https://top-skill.web.app with real email/password accounts **before the 2026-10-17 18:00 code freeze**.

## 5. Run log

| Date | Env | Commit | Result | Notes |
|---|---|---|---|---|
| 2026-10-15 | prod | – | infra only | `provision.sh` run; Firestore rules + 14 composite indexes + `rate_limits.expires_at` TTL deployed; backend/web not deployed yet |
