# TopSkill — Demo (3-minute video)

Owner: [deploy] + lead. Storyline source: [`SPEC.md`](SPEC.md) §1.2. Recorded from the **frozen prod build** (https://top-skill.web.app) on 2026-10-18.

## Script

| t | Beat | On screen | Voice-over (draft) |
|---|---|---|---|
| 0:00–0:15 | Problem | Title card → "Expert knowledge is locked in people and PDFs. Enterprise agents can't hire experts." | "Every company runs on expert know-how — but it lives in people's heads and PDFs, and AI agents can't hire those experts." |
| 0:15–1:00 | Creator publishes in about a minute | `/studio/new`: drop `visa-advisor/SKILL.md` + `visa_guide.pdf` → Gemini drafts every field (≈ 15–20 s, measured 17 s) → Generate avatar (≈ 10–15 s, measured 14 s) → pricing: set 1.00 per round if Gemini suggested another price (it suggested 1.50 in the local run) → pricing line "Visitors pay 1.00 per round, first 2 rounds free; you receive 0.80"; **turn "Callable by AI agents (A2A)" off** → Go live: **"List in the marketplace" off** → Publish → "Published in N s" (≈ 60–70 s; cut the model waits in the edit) | "A visa consultant drops in her playbook. Gemini drafts the whole agent — persona, pricing, starter questions, even the lead form — and paints an avatar. Publish." |
| 1:00–1:45 | Visitor gets grounded answers + record | Seeded, pre-ingested `/a/visa-advisor` (Overview shows "Knowledge: ready"): starter question → streamed answer with `[1]` → Sources "visa_guide.pdf · p. 2" + Google Search chips → visitor states nationality/budget → tool line "Saved notes" (expanded: "3 fields") → round 3 "Charged 1.00 · balance 99.00" → creator Records tab row → Export CSV | "Answers cite her own documents down to the page, plus fresh Google Search sources. The agent fills a lead record as it talks. Two free rounds, then one credit per round." |
| 1:45–2:35 | Enterprise Assistant pays agents over A2A | `/assistant`: "Our new hire from Brazil needs a work visa plan, a resume review and a quick check of the offer letter. Resume: senior data engineer, 6 years Python and Spark, São Paulo. Offer: Berlin, EUR 85k, start in March." (the facts let the resume reviewer answer instead of asking for the resume) → Authorise budget 20.00, max 5.00/call, 30 min → discovers the seeded Agent Cards (one search per need) → visa-advisor charged 2.00 → resume-reviewer charged 2.00 → contract-reviewer **rejected · mandate_exceeds_max · Not charged** → final answer notes the skipped check; budget bar 4.00 / 20.00 | "Other agents can hire these experts too. The assistant gets a signed mandate with a budget, discovers specialists over A2A and pays per call. The contract reviewer costs more than the per-call cap, so the platform rejects it — and nothing is charged." |
| 2:35–3:00 | Creator sees earnings | `/studio`: Earned today rises (visitor rounds + A2A income), ledger rows with counterparty + memo → architecture slide | "Creators earn 80% of every round and every agent call. Built on Gemini, ADK, A2A, Cloud Run and Firestore." |

## Demo accounts (prod)

| Role | Account | Notes |
|---|---|---|
| Creator = seed owner (publishes on video, owns `visa-advisor`, `resume-reviewer`, `contract-reviewer`) | _to fill_ | creator role; balance > 0; seeded by `backend/scripts/seed_demo.py --owner-uid <this account>`; `contract-reviewer` A2A price 6.00. One account, so the 2:35 `/studio` dashboard shows the visitor rounds and A2A income of beats 3 and 4 (income is summed per owner). The wizard copy gets its own auto slug (`visa-advisor` is taken). |
| Visitor | _to fill_ | **not** the creator (an owner's own rounds are free and uncounted); fresh account (2 free rounds unused) or a new one per take |
| Assistant user | _to fill_ | **not** the creator: an owner calling their own agent pays 0, so `contract-reviewer` would not be rejected and nothing would be earned; balance ≥ 20.00 |

Passwords live in the owner's password manager, never in this repo.

## Warm-up (30 min before recording)

1. `curl -s https://topskill-api-920441209758.asia-southeast1.run.app/health` → `env: prod`, expected sha (not `/healthz`: Cloud Run reserves paths ending in `z`).
2. `deploy/scripts/smoke_prod.sh` (with credentials) → `smoke OK`.
3. Open `/a/visa-advisor` once and ask one throw-away question (warms ADK imports, model and File Search paths) with a non-recording account.
4. Run one Enterprise Assistant turn end-to-end with a throw-away mandate.
   - The discovery cards must show the **seeded** `visa-advisor` (2.00), `resume-reviewer` (2.00) and `contract-reviewer` (6.00) only. Any other visa agent (an earlier wizard copy, a judge's or an E2E test agent) that is listed and A2A-enabled competes with them: it may be hired instead (wrong price, wrong owner, budget bar ≠ 4.00 / 20.00). Pause, unlist or delete such agents, or turn their A2A off, before recording (`curl -s "$API/api/public/a2a/directory?q=visa"` lists what the assistant can find).
   - The Assistant user must not own any of the three seeded agents (check the owner in `/studio` of the creator account, or that the 6.00 contract call is rejected).
5. Check "Knowledge: ready" on the seeded `visa-advisor` (and on the wizard copy if it is shown).
6. Browser: 1440 × 900 window, zoom 100%, light theme, bookmarks bar hidden, notifications off.
7. Top up demo accounts with "Add demo credits" if balances drifted.

## Recording notes

- Record the 0:15 wizard beat with "List in the marketplace" **off** and "Callable by AI agents (A2A)" **off** (or pause the copy right after the take): the wizard enables both by default, and a listed A2A copy of the visa skill is found by the assistant at 1:45.
- "Earned today" on `/studio` is the current **UTC** day: record after 08:00 Asia/Shanghai, and expect warm-up income from the same UTC day in the figure.
- Open `/a/visa-advisor` by URL for the 1:00 beat: the marketplace lists newest first, so the seeded visa agent is not the first card.

- If a model call 429s, re-take; the model chain falls back but the video should show a clean run.
- Keep the rejected A2A call on screen for 5–10 s; point at the unchanged budget bar.
- After recording: scale Cloud Run min instances to 0 if the project pauses (SPEC §13.5).
