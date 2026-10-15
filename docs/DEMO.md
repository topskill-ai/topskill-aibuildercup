# TopSkill — Demo (3-minute video)

Owner: [deploy] + lead. Storyline source: [`SPEC.md`](SPEC.md) §1.2. Recorded from the **frozen prod build** (https://top-skill.web.app) on 2026-10-18.

## Script

| t | Beat | On screen | Voice-over (draft) |
|---|---|---|---|
| 0:00–0:15 | Problem | Title card → "Expert knowledge is locked in people and PDFs. Enterprise agents can't hire experts." | "Every company runs on expert know-how — but it lives in people's heads and PDFs, and AI agents can't hire those experts." |
| 0:15–1:00 | Creator publishes in 60 s | `/studio/new`: drop `visa-advisor/SKILL.md` + `visa_guide.pdf` → Gemini drafts every field (≈ 9 s) → Generate avatar (≈ 8 s) → pricing line "Visitors pay 1.00 per round, first 2 rounds free; you receive 0.80" → Publish → "Published in 52 s" | "A visa consultant drops in her playbook. Gemini drafts the whole agent — persona, pricing, starter questions, even the lead form — and paints an avatar. Publish." |
| 1:00–1:45 | Visitor gets grounded answers + record | Seeded, pre-ingested `/a/visa-advisor` (Overview shows "Knowledge: ready"): starter question → streamed answer with `[1]` → Sources "visa_guide.pdf · p. 2" + Google Search chips → visitor states nationality/budget → "Saved 3 fields" → round 3 "Charged 1.00 · balance 99.00" → creator Records tab row → Export CSV | "Answers cite her own documents down to the page, plus fresh Google Search sources. The agent fills a lead record as it talks. Two free rounds, then one credit per round." |
| 1:45–2:35 | Enterprise Assistant pays agents over A2A | `/assistant`: "Our new hire from Brazil needs a work visa plan, a resume review and a quick check of the offer letter" → Authorise budget 20.00, max 5.00/call, 30 min → discovers 3 Agent Cards → visa-advisor charged 2.00 → resume-reviewer charged 2.00 → contract-reviewer **rejected · mandate_exceeds_max · Not charged** → final answer notes the skipped check; budget bar 4.00 / 20.00 | "Other agents can hire these experts too. The assistant gets a signed mandate with a budget, discovers specialists over A2A and pays per call. The contract reviewer costs more than the per-call cap, so the platform rejects it — and nothing is charged." |
| 2:35–3:00 | Creator sees earnings | `/studio`: Earned today rises (visitor rounds + A2A income), ledger rows with counterparty + memo → architecture slide | "Creators earn 80% of every round and every agent call. Built on Gemini, ADK, A2A, Cloud Run and Firestore." |

## Demo accounts (prod)

| Role | Account | Notes |
|---|---|---|
| Creator (publishes on video) | _to fill_ | creator role; balance > 0 |
| Seed owner of `visa-advisor`, `resume-reviewer`, `contract-reviewer` | _to fill_ | seeded by `backend/scripts/seed_demo.py`; `contract-reviewer` A2A price 6.00 |
| Visitor | _to fill_ | fresh account (2 free rounds unused) or a new one per take |
| Assistant user | _to fill_ | balance ≥ 20.00 |

Passwords live in the owner's password manager, never in this repo.

## Warm-up (30 min before recording)

1. `curl -s https://topskill-api-920441209758.asia-southeast1.run.app/healthz` → `env: prod`, expected sha.
2. `deploy/scripts/smoke_prod.sh` (with credentials) → `smoke OK`.
3. Open `/a/visa-advisor` once and ask one throw-away question (warms ADK imports, model and File Search paths) with a non-recording account.
4. Run one Enterprise Assistant turn end-to-end with a throw-away mandate.
5. Check "Knowledge: ready" on the seeded `visa-advisor` and on the wizard copy if it is shown.
6. Browser: 1440 × 900 window, zoom 100%, light theme, bookmarks bar hidden, notifications off.
7. Top up demo accounts with "Add demo credits" if balances drifted.

## Recording notes

- If a model call 429s, re-take; the model chain falls back but the video should show a clean run.
- Keep the rejected A2A call on screen for 5–10 s; point at the unchanged budget bar.
- After recording: scale Cloud Run min instances to 0 if the project pauses (SPEC §13.5).
