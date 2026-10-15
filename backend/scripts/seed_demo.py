"""Seed the demo agents through the service functions (SPEC §1.2, §12.3, §12.4 publish).

Creates, for one owner account, the three agents of the demo storyline:

* ``visa-advisor``      – 1.00 per round, 2 free rounds, A2A 2.00, records on, knowledge ``visa_guide.pdf``
* ``resume-reviewer``   – 1.00 per round, 1 free round, A2A 2.00
* ``contract-reviewer`` – 3.00 per round, 1 free round, A2A 6.00 (above the default 5.00 mandate cap,
  so the Enterprise Assistant's call is rejected with ``mandate_exceeds_max`` and not charged)

Run from ``backend/`` (settings come from the environment / ``backend/.env``)::

    # local emulators (dev)
    uv run python -m scripts.seed_demo --owner-uid demo-creator --owner-name "Ana Silva"

    # prod (laptop, ADC): FIRESTORE_PROJECT=top-skill STORAGE_BACKEND=gcs KNOWLEDGE_BACKEND=file_search
    # GEMINI_API_KEY=... grpc_proxy=http://127.0.0.1:6152 \
    #   uv run python -m scripts.seed_demo --owner-uid <firebase uid of the seed owner>

The script is idempotent: an existing agent with the same slug and owner is left alone (``--update``
re-applies the listing and builds a new version). Knowledge is uploaded through the knowledge API
in-process (as the owner) when the knowledge package is available and the agent has no ready file yet;
the script then waits until ingestion finishes (``--wait`` seconds).
"""

# ruff: noqa: E501  (long English prose in data literals)

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from topskill.agents import service as agents
from topskill.agents.avatar import store_avatar
from topskill.agents.deps import billing
from topskill.agents.schemas import ConfigPatch, CreateAgentRequest, UpdateAgentRequest
from topskill.auth import CurrentUser, ensure_user, to_current_user
from topskill.db import Col, get_db, utcnow
from topskill.domain import AgentDoc
from topskill.repo import get_agent_by_slug
from topskill.skills.service import create_skill_from_entries

log = logging.getLogger("seed_demo")

DATA = Path(__file__).resolve().parent / "demo_data"
SKIP_NAMES = {"__pycache__", ".DS_Store"}


@dataclass
class DemoAgent:
    slug: str
    skill_dir: str
    creator_name: str
    creator_headline: str
    listing: dict[str, Any]
    pricing: dict[str, Any]
    config: dict[str, Any]
    knowledge: list[str] = field(default_factory=list)


VISA_RECORD_FIELDS = [
    {
        "key": "nationality",
        "label": "Nationality",
        "type": "text",
        "description": "The person's nationality (and second nationality, if any).",
        "required": True,
        "show_in_table": True,
    },
    {
        "key": "destination_country",
        "label": "Destination",
        "type": "single_choice",
        "description": "Country where the person will work.",
        "options": [
            {"key": "germany", "label": "Germany"},
            {"key": "netherlands", "label": "Netherlands"},
            {"key": "portugal", "label": "Portugal"},
            {"key": "united_kingdom", "label": "United Kingdom"},
            {"key": "other", "label": "Other"},
        ],
        "required": True,
        "show_in_table": True,
    },
    {
        "key": "job_title",
        "label": "Job title",
        "type": "text",
        "description": "The role in the job offer.",
    },
    {
        "key": "start_date",
        "label": "Planned start",
        "type": "text",
        "description": "Planned first working day, as stated by the visitor (e.g. 'early March 2027').",
    },
    {
        "key": "budget_eur",
        "label": "Budget (EUR)",
        "type": "number",
        "description": "Total budget for the visa process in euros.",
        "min": 0,
        "max": 100000,
        "show_in_table": True,
    },
    {
        "key": "family_relocating",
        "label": "Family relocating",
        "type": "boolean",
        "description": "Whether a spouse or children relocate too.",
    },
    {
        "key": "contact_email",
        "label": "Email",
        "type": "text",
        "description": "Email for a follow-up, only if the visitor offers it.",
        "pii": True,
    },
]

DEMO_AGENTS: list[DemoAgent] = [
    DemoAgent(
        slug="visa-advisor",
        skill_dir="visa-advisor",
        creator_name="Ana Silva",
        creator_headline="Immigration consultant · Northbridge Mobility",
        listing={
            "title": "Visa Advisor",
            "tagline": "Work visa plans for international hires, with documents, timeline and budget.",
            "description": (
                "Planning a move abroad for a new job, or hiring someone from another country? Visa Advisor "
                "compares the realistic work permit routes for your case, lists every document you need with "
                "lead times, and gives a week-by-week timeline and budget. Answers cite Northbridge Mobility's "
                "2026 visa guide and current public sources. It does not replace a lawyer for refusals, "
                "appeals or criminal records."
            ),
            "greeting": (
                "Hi, I'm Visa Advisor from Ana Silva's practice. I plan work visas for new hires. "
                "Which country is the job in, and what is your nationality?"
            ),
            "input_placeholder": "Describe the job offer and your nationality",
            "starter_questions": [
                {
                    "label": "Work visa plan for Germany",
                    "prompt": (
                        "I'm a software engineer from Brazil with a job offer in Berlin starting in three "
                        "months. Which visa route fits and what's the timeline?"
                    ),
                },
                {
                    "label": "Which documents do I need?",
                    "prompt": "Which documents do I need for a work visa, and which ones take the longest?",
                },
                {
                    "label": "What will it cost?",
                    "prompt": "What does the whole work visa process cost, including your service packages?",
                },
            ],
            "capabilities": [
                {
                    "icon": "plane",
                    "name": "Route assessment",
                    "description": "Compares work permit routes and recommends the best fit for the case.",
                },
                {
                    "icon": "clipboard-list",
                    "name": "Document checklist",
                    "description": "Lists every document with lead times and the usual longest poles.",
                },
                {
                    "icon": "calculator",
                    "name": "Timeline and budget",
                    "description": "Week-by-week plan to the first working day with a cost estimate.",
                },
                {
                    "icon": "users",
                    "name": "Family relocation",
                    "description": "Covers spouses and children applying with the main applicant.",
                },
            ],
            "accent_color": "blue",
            "listed": True,
        },
        pricing={
            "mode": "per_round",
            "price_cents": 100,
            "free_rounds": 2,
            "a2a_enabled": True,
            "a2a_price_cents": 200,
        },
        config={
            "soul": (
                "You are Visa Advisor, the work-visa assistant of Ana Silva's immigration practice, Northbridge "
                "Mobility. You serve HR teams hiring internationally and professionals who received a job "
                "offer abroad. First understand the case: nationality, destination country and city, job "
                "title, gross salary, start date, degree and whether family relocates. Ask for what is "
                "missing, one question at a time. Then recommend the best work permit route and explain why it "
                "beats the alternatives, list the documents with lead times, and give a week-by-week timeline "
                "and a budget split into government fees, translations and our service fee. End with the three "
                "most important actions for this week. Prefer the Northbridge guide for our processes, fees "
                "and observed timelines; use web search for current government thresholds and fees and say "
                "when a figure may have changed. Never guarantee approval. Refer previous refusals, criminal "
                "records, overstays, asylum and appeals to a licensed lawyer. Never ask for passport numbers."
            ),
            "web_search_enabled": True,
            "record_schema": {"fields": VISA_RECORD_FIELDS},
        },
        knowledge=["visa_guide.pdf"],
    ),
    DemoAgent(
        slug="resume-reviewer",
        skill_dir="resume-reviewer",
        creator_name="Maya Lee",
        creator_headline="Technical recruiter",
        listing={
            "title": "Resume Reviewer",
            "tagline": "A recruiter's honest resume score with rewrites that get you shortlisted.",
            "description": (
                "Paste a resume and the target role. Resume Reviewer screens it the way an experienced "
                "technical recruiter does: a six-second first impression, a 25-point rubric score, the three "
                "highest-impact rewrites, and local resume conventions when you relocate. It never invents "
                "experience or numbers."
            ),
            "greeting": (
                "Hi, I'm Resume Reviewer. Paste your resume and tell me the role you're targeting, "
                "and I'll score it like a recruiter would."
            ),
            "input_placeholder": "Paste a resume and the target role",
            "starter_questions": [
                {
                    "label": "Score my resume",
                    "prompt": "Can you score my resume for a senior backend engineer role? I'll paste it next.",
                },
                {
                    "label": "Rewrite my bullet points",
                    "prompt": "How do I rewrite my experience bullets so they show impact instead of duties?",
                },
                {
                    "label": "Resume rules for Germany",
                    "prompt": "What should a resume look like for a job application in Germany?",
                },
            ],
            "capabilities": [
                {
                    "icon": "search",
                    "name": "Six-second scan",
                    "description": "What a recruiter notices first, including red flags.",
                },
                {
                    "icon": "chart-line",
                    "name": "Rubric score",
                    "description": "Five criteria scored 1 to 5 with a clear verdict.",
                },
                {
                    "icon": "file-text",
                    "name": "Impact rewrites",
                    "description": "Before and after bullets with strong verbs and numbers.",
                },
            ],
            "accent_color": "emerald",
            "listed": True,
        },
        pricing={
            "mode": "per_round",
            "price_cents": 100,
            "free_rounds": 1,
            "a2a_enabled": True,
            "a2a_price_cents": 200,
        },
        config={
            "soul": (
                "You are Resume Reviewer, built from technical recruiter Maya Lee's screening playbook. You help "
                "candidates and hiring teams judge a resume against a target role. First get the resume text "
                "and the target role (title, seniority, country); ask once for whatever is missing. Then give "
                "the six-second scan, score the resume with the five-criterion rubric (1 to 5 each, total out "
                "of 25), propose the three highest-impact rewrites as Before and After bullets, add local "
                "conventions for relocations, and finish with a one-line verdict. Never invent experience, "
                "employers, degrees or numbers; use placeholders like [X%] and ask the user to fill them in. "
                "Never comment on age, gender, nationality, religion, health or family status. Keep reviews "
                "under 400 words unless asked for more."
            ),
            "web_search_enabled": False,
            "record_schema": None,
        },
    ),
    DemoAgent(
        slug="contract-reviewer",
        skill_dir="contract-reviewer",
        creator_name="Priya Raman",
        creator_headline="Employment lawyer",
        listing={
            "title": "Contract Reviewer",
            "tagline": "Clause-by-clause review of employment contracts and offer letters.",
            "description": (
                "Paste an employment contract or offer letter and say which side you are on. Contract Reviewer "
                "summarises the deal, checks every important clause, explains the top three risks in plain "
                "language and proposes balanced wording. For international hires it checks visa sponsorship "
                "and relocation terms. It is a first-pass review, not legal advice for disputes."
            ),
            "greeting": (
                "Hi, I'm Contract Reviewer. Paste the contract or offer letter, and tell me whether you're the "
                "employer or the employee and which country's law applies."
            ),
            "input_placeholder": "Paste the contract or offer letter",
            "starter_questions": [
                {
                    "label": "Review my offer letter",
                    "prompt": "Can you review my offer letter? I'm the employee and German law applies.",
                },
                {
                    "label": "Is this non-compete fair?",
                    "prompt": "How do I tell whether a non-compete clause in my contract is reasonable?",
                },
                {
                    "label": "Relocation clauses",
                    "prompt": "What should an offer letter say about visa sponsorship and relocation costs?",
                },
            ],
            "capabilities": [
                {
                    "icon": "scale",
                    "name": "Clause check",
                    "description": "Marks each key clause as fine, needs attention or missing.",
                },
                {
                    "icon": "shield-check",
                    "name": "Top risks",
                    "description": "The three most important issues with balanced replacement wording.",
                },
                {
                    "icon": "globe",
                    "name": "Relocation terms",
                    "description": "Visa sponsorship, relocation support and permit-dependent start dates.",
                },
            ],
            "accent_color": "violet",
            "listed": True,
        },
        pricing={
            "mode": "per_round",
            "price_cents": 300,
            "free_rounds": 1,
            "a2a_enabled": True,
            "a2a_price_cents": 600,
        },
        config={
            "soul": (
                "You are Contract Reviewer, built from employment lawyer Priya Raman's review playbook. You give "
                "a structured first-pass review of employment contracts and offer letters for employers and "
                "employees. First get the document, the user's side and the governing law; ask for whatever is "
                "missing. Then summarise the deal in five bullets, go through the clause checklist marking each "
                "item as fine, needs attention or missing, explain the top three risks in plain language with "
                "balanced replacement wording, and for international hires check visa sponsorship, relocation "
                "support and permit-dependent start dates. State the governing law you assumed. Never call a "
                "contract legal or safe to sign, never invent statutes or case law, and recommend a local "
                "lawyer for disputes, terminations in progress or litigation."
            ),
            "web_search_enabled": True,
            "record_schema": None,
        },
    ),
]


# --------------------------------------------------------------------------- helpers


def read_skill_folder(path: Path) -> list[tuple[str, bytes]]:
    """``[(relative path with the folder name as top directory, bytes)]`` like a browser folder upload."""
    entries: list[tuple[str, bytes]] = []
    for file in sorted(path.rglob("*")):
        if not file.is_file() or any(part in SKIP_NAMES for part in file.parts):
            continue
        entries.append((f"{path.name}/{file.relative_to(path).as_posix()}", file.read_bytes()))
    return entries


async def ensure_owner(uid: str, email: str | None, name: str | None, headline: str) -> CurrentUser:
    user = await ensure_user(uid, email=email, name=name, role="creator", update_role=True)
    updates: dict[str, Any] = {}
    if name and user.display_name != name:
        updates["display_name"] = name
    profile = dict(user.creator_profile or {"headline": "", "bio": ""})
    if headline and profile.get("headline") != headline:
        profile["headline"] = headline
        updates["creator_profile"] = profile
    if updates:
        updates["updated_at"] = utcnow()
        await get_db().collection(Col.USERS).document(uid).update(updates)
        user = user.model_copy(update=updates)
    return to_current_user(user)


async def _avatar_id(slug: str) -> str | None:
    file = DATA / "avatars" / f"{slug}.webp"
    if not file.exists():
        return None
    return (await store_avatar(file.read_bytes()))["avatar_id"]


def _request(spec: DemoAgent, slug: str, skill_id: str, avatar_id: str | None) -> CreateAgentRequest:
    return CreateAgentRequest.model_validate(
        {
            **spec.listing,
            "skill_ids": [skill_id],
            "slug": slug,
            "avatar_id": avatar_id,
            "creator_name": spec.creator_name,
            "creator_headline": spec.creator_headline,
            "pricing": spec.pricing,
            "config": spec.config,
        }
    )


async def _update(owner: CurrentUser, agent: AgentDoc, spec: DemoAgent) -> AgentDoc:
    skill = await create_skill_from_entries(owner.uid, read_skill_folder(DATA / spec.skill_dir))
    avatar_id = await _avatar_id(spec.slug)
    patch = UpdateAgentRequest.model_validate(
        {
            **spec.listing,
            "avatar_id": avatar_id,
            "creator_name": spec.creator_name,
            "creator_headline": spec.creator_headline,
            "pricing": spec.pricing,
            "config": ConfigPatch.model_validate({**spec.config, "skill_ids": [skill["id"]]}).model_dump(
                exclude_unset=True
            ),
        }
    )
    agent = await agents.update_agent(agent, owner, patch)
    return await agents.create_version(agent, owner, "Seed update")


async def seed_agent(
    owner: CurrentUser, spec: DemoAgent, *, suffix: str, update: bool
) -> tuple[AgentDoc, str]:
    slug = f"{spec.slug}-{suffix}" if suffix else spec.slug
    existing = await get_agent_by_slug(slug)
    if existing is not None:
        if existing.owner_uid != owner.uid:
            raise SystemExit(f"slug {slug!r} belongs to another account ({existing.owner_uid}); aborting")
        if not update:
            return existing, "exists"
        return await _update(owner, existing, spec), "updated"
    skill = await create_skill_from_entries(owner.uid, read_skill_folder(DATA / spec.skill_dir))
    avatar_id = await _avatar_id(spec.slug)
    agent = await agents.create_agent(owner, _request(spec, slug, skill["id"], avatar_id))
    return agent, "created"


# --------------------------------------------------------------------------- knowledge (in-process API)


async def upload_knowledge(owner: CurrentUser, agent: AgentDoc, files: list[str], wait_s: float) -> str:
    """Upload missing knowledge files through ``POST /api/agents/{id}/knowledge`` in-process (as the owner)
    and wait for ingestion. Returns a short status line."""
    import httpx

    from topskill import tasks
    from topskill.auth import get_current_user
    from topskill.main import create_app

    app = create_app()
    if not any(r.endswith("knowledge.router:router") for r in app.state.mounted_routers):
        return "skipped (knowledge package not available)"

    async def _owner() -> CurrentUser:
        return owner

    app.dependency_overrides[get_current_user] = _owner
    base = f"/api/agents/{agent.id}/knowledge"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://seed") as client:
        listing = (await client.get(base)).json()
        have = {f["filename"]: f["status"] for f in listing.get("items", [])}
        for name in files:
            if have.get(name) in ("ready", "processing"):
                continue
            data = (DATA / "knowledge" / name).read_bytes()
            resp = await client.post(base, files=[("files", (name, data, "application/pdf"))])
            if resp.status_code >= 300:
                return f"upload failed: {resp.status_code} {resp.text[:200]}"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_s
        statuses: dict[str, str] = {}
        while True:
            items = (await client.get(base)).json().get("items", [])
            statuses = {f["filename"]: f["status"] for f in items}
            if all(s != "processing" for s in statuses.values()) or loop.time() > deadline:
                break
            await asyncio.sleep(2)
    await tasks.drain(timeout=10)
    return ", ".join(f"{k}: {v}" for k, v in statuses.items()) or "no files"


# --------------------------------------------------------------------------- main


async def run(args: argparse.Namespace) -> list[dict[str, str]]:
    owner = await ensure_owner(args.owner_uid, args.owner_email, args.owner_name, args.owner_headline)
    await billing().get_or_open_wallet(owner.uid)
    results: list[dict[str, str]] = []
    for spec in DEMO_AGENTS:
        agent, outcome = await seed_agent(owner, spec, suffix=args.slug_suffix, update=args.update)
        knowledge = "-"
        if spec.knowledge and not args.skip_knowledge:
            knowledge = await upload_knowledge(owner, agent, spec.knowledge, args.wait)
        results.append({"slug": agent.slug, "agent_id": agent.id, "outcome": outcome, "knowledge": knowledge})
        print(f"{outcome:8s} {agent.slug:32s} {agent.id}  knowledge: {knowledge}")
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--owner-uid", default="demo-creator", help="uid of the seed owner (Firebase uid in prod)")
    p.add_argument("--owner-email", default=None, help="email stored on first creation of the user doc")
    p.add_argument("--owner-name", default="Ana Silva", help="display name of the owner")
    p.add_argument("--owner-headline", default="Immigration consultant", help="creator headline")
    p.add_argument("--slug-suffix", default="", help="append '-<suffix>' to every slug (tests, rehearsals)")
    p.add_argument("--update", action="store_true", help="re-apply listings and build a new version")
    p.add_argument("--skip-knowledge", action="store_true", help="do not upload knowledge files")
    p.add_argument("--wait", type=float, default=180.0, help="seconds to wait for knowledge ingestion")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    args = parse_args(argv)
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
