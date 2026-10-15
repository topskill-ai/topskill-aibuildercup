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
| 1 | Creator signs in (local: Developer login `creator-e2e` / role creator; prod: email + password). Seed the demo agents with this account as owner (`seed_demo.py --owner-uid`), so step 14 shows the income of steps 8–13; the visitor (step 7) and the assistant user (step 12) are other accounts | Studio dashboard, balance 100.00 | `studio-empty` |
| 2 | `/studio/new`: drop `backend/scripts/demo_data/visa-advisor/` + `visa_guide.pdf` | Draft fields fill in (≈ 15–20 s), timer running | `wizard-draft` |
| 3 | Generate avatar | Avatar appears (≈ 10–15 s) | `wizard-avatar` |
| 4 | Pricing step: set 1.00 per round if the draft suggested another price; turn **"Callable by AI agents (A2A)" off** | "Visitors pay 1.00 per round, first 2 rounds free; you receive 0.80" | `wizard-pricing` |
| 5 | Go live: **"List in the marketplace" off**, then Publish (a listed, A2A-enabled copy of the visa skill competes with the seeded `visa-advisor` in step 13) | Redirect to agent Overview, "Published in N s" toast | `agent-overview` |
| 6 | Wait for knowledge | Overview badge "Knowledge: ready" | `knowledge-ready` |
| 7 | Visitor signs in (other account), opens `/a/<slug>` | Greeting, 3 starter questions, billing hint "2 free rounds left" | `agent-page` |
| 8 | Round 1 (starter question) | Streamed answer with `[1]` markers, Sources: `visa_guide.pdf · p. N`, web chips + search entry point, "Free round 1 of 2" | `chat-citations` |
| 9 | Round 2: state nationality / budget | Tool line "Saved notes" (expanded: "N fields"), "Free round 2 of 2" | `chat-record` |
| 10 | Round 3 | "Charged 1.00 · round 3", balance 99.00 | `chat-charged` |
| 11 | Creator → Records tab | New row with saved fields; Export CSV downloads (BOM, columns) | `records`, CSV |
| 12 | `/assistant`: authorise budget 20.00 / max 5.00 / 30 min | Mandate card | `assistant-mandate` |
| 13 | Ask the Brazil new-hire question (with the resume/offer facts from DEMO.md) | Discover cards with the seeded agents (no other visa agent: see DEMO.md warm-up 4) → `visa-advisor` charged 2.00 → `resume-reviewer` charged 2.00 → `contract-reviewer` **rejected · mandate_exceeds_max · Not charged** → final answer; budget bar 4.00 / 20.00 | `assistant-timeline` |
| 14 | Creator `/studio` | Earned today includes visitor + A2A income (separate categories), recent income rows | `dashboard-income` |
| 15 | `/wallet` (visitor and assistant user) | Ledger rows with categories, counterparty, memo | `wallet-ledger` |

Error paths to spot-check once: visitor with 0.40 credits → Paywall dialog (`visitor_insufficient_points`) with demo top-up; paused agent → "Temporarily unavailable"; Stop button mid-stream → "Not charged (stopped)".

## 4. Prod checks after every deploy

0. Deploy from a dedicated clean worktree (`git worktree add --detach ../topskill.worktrees/deploy-prod main`, then `git -C ../topskill.worktrees/deploy-prod checkout --detach <sha>`): the script refuses a tree with untracked files (they would enter the Cloud Build context), and other work in the main checkout writes screenshots and drafts. Deploy Firestore indexes **before** the backend; the script waits until every index is READY (`INDEX_WAIT_S`, default 900 s), because a new revision's startup knowledge sweep needs the `knowledge.status` collection-group index. If an index was added after a backend deploy, redeploy (or restart the revision) once it is READY so the sweep runs.
1. `deploy/scripts/deploy_backend.sh` checks that the Ready revision serves `api:<sha>` with 100% traffic, that `/health` reports the sha (when the alias is present; `/healthz` is reserved by Cloud Run's front end and only reachable by the startup probe) and that `/api/public/meta` is 200.
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
| 2026-10-15 | prod | 49f061d | P2 ship | `provision.sh --yes` applied the P2 grants (dataset `dataEditor` now via BigQuery DCL `GRANT`; `bq add-iam-policy-binding --dataset` needs allowlisting), scheduler SA + job `topskill-analytics-sync`. Revision 00006 (5b442f4) answered every CORS preflight with 500 when tracing was on → `TRACING_ENABLED=false` on 00007, fixed in 49f061d (revision 00008, tracing on). `smoke_prod.sh` OK; Model Armor badge + mic on `/a/visa-advisor`; agent cards 200; Cloud Trace has spans for 12/17 smoke requests (health/meta/preflight sampled at 1 %, `docs/screenshots/p2/prod-smoke-traces.json`); `/api/internal/analytics/sync` 401 without OIDC, scheduler runs 200 and filled `topskill_analytics` |
| 2026-10-15 | local | 49f061d | P2 E2E pass | Ports 8191/9191/4491/4591, API 8291, web 5291, `KNOWLEDGE_BACKEND=file_search`, tracing + Model Armor + analytics (`topskill_analytics_dev`) on. (a) starter question → `search_knowledge` + `visa_guide.pdf` page citations; (b) prompt injection blocked by Model Armor, not charged, free round kept; (c) voice: caption + spoken answer with knowledge search + one settled free round (needs `WSS_PROXY=socks5h://127.0.0.1:6153` + `python-socks` behind the local proxy; Chrome's `--use-file-for-fake-audio-capture` produced no speech, an injected `getUserMedia` did); (d) "Ask your data" daily-income question → SQL + line chart + table; (e) turn traces fetched from the Cloud Trace API (`docs/screenshots/p2/local-e-turn-traces.json`). Screenshots in `docs/screenshots/p2/` |
