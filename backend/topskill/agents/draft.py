"""Gemini-drafted agent listing from selected skills (SPEC §8.7 Draft).

Structured output with ``response_schema=DraftModel`` on the ``draft`` chain (``fast``) or ``draft_pro``
chain (``best``), thinking ``low``. The schema has no length limits on purpose: overlong values are clipped
afterwards instead of failing the whole draft. Draft writes nothing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from topskill import gemini
from topskill.agents import deps
from topskill.config import get_settings
from topskill.domain import (
    AccentColor,
    Capability,
    CapabilityIcon,
    FieldType,
    PricingMode,
    RecordField,
    RecordOption,
    StarterQuestion,
)
from topskill.errors import ApiError, Codes
from topskill.ratelimit import hit
from topskill.skills.parser import clip

log = logging.getLogger(__name__)

USER_MESSAGE_MAX = 16_000
MIN_BODY_CHARS = 200
NAME_MAX = 100
DESCRIPTION_MAX = 300
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
CHOICE_TYPES = ("single_choice", "multi_choice")


# --------------------------------------------------------------------------- model schema


class DraftStarterQuestion(BaseModel):
    label: str = Field(description="Short button label in the visitor's own voice, at most 60 characters.")
    prompt: str = Field(description="The full question the visitor sends, at most 300 characters.")


class DraftCapability(BaseModel):
    icon: CapabilityIcon = Field(description="Icon that best fits this capability.")
    name: str = Field(description="Capability name, 2-4 words, at most 30 characters.")
    description: str = Field(description="One sentence, at most 120 characters.")


class DraftPricing(BaseModel):
    mode: PricingMode = Field(description="'per_round' for paid expert help, 'free' for trivial helpers.")
    price_cents: int = Field(description="Suggested price per round in cents of credits, 50 to 1000.")
    free_rounds: int = Field(description="Free rounds per visitor before charging, 0 to 5.")
    rationale: str = Field(description="Why this price, at most 200 characters.")


class DraftRecordOption(BaseModel):
    key: str = Field(description="Option key, lowercase snake_case, starts with a letter.")
    label: str = Field(description="Option label, at most 40 characters.")


class DraftRecordField(BaseModel):
    key: str = Field(description="Field key, lowercase snake_case matching ^[a-z][a-z0-9_]{0,39}$.")
    label: str = Field(description="Column label, at most 40 characters.")
    type: FieldType
    description: str = Field(description="What to record and how, at most 300 characters.")
    options: list[DraftRecordOption] | None = Field(
        None, description="Required for single_choice and multi_choice (2-8 options); null otherwise."
    )
    required: bool = Field(description="True when the expert needs this to help the visitor.")
    pii: bool = Field(description="True for personal data such as name, email or phone.")
    show_in_table: bool = Field(description="True for the 2-3 most useful columns.")
    min: float | None = Field(None, description="Minimum for number fields, else null.")
    max: float | None = Field(None, description="Maximum for number fields, else null.")


class DraftModel(BaseModel):
    title: str = Field(description="Agent name, 2-6 words, at most 40 characters, plain language, no quotes.")
    tagline: str = Field(description="One-line value proposition, at most 80 characters.")
    description: str = Field(description="Marketplace description for visitors, at most 1000 characters.")
    soul: str = Field(description="System persona in second person ('You are...'), 120-300 words.")
    greeting: str = Field(description="First message: one line, at most 60 words, no Markdown.")
    input_placeholder: str = Field(description="Chat input placeholder, at most 12 words.")
    starter_questions: list[DraftStarterQuestion] = Field(description="Exactly 3 starter questions.")
    capabilities: list[DraftCapability] = Field(description="3 or 4 capabilities.")
    accent_color: AccentColor = Field(description="Brand accent color that suits the domain.")
    pricing: DraftPricing
    record_fields: list[DraftRecordField] = Field(
        description="0-6 fields, only when the skill naturally collects information from the visitor."
    )
    web_search_recommended: bool = Field(description="True when fresh facts from the web help this agent.")
    avatar_prompt: str = Field(
        description="Image prompt for a flat illustration avatar, no text, at most 300 characters."
    )


SYSTEM_INSTRUCTION = """\
You turn an expert's skill (a playbook written as SKILL.md) into a public marketplace listing for a paid AI
agent on TopSkill. Visitors chat with the agent on the web; other AI agents can call it over A2A.
Write everything in English and use only facts present in the skill content. Never invent credentials,
guarantees, prices of third parties or legal promises.

Field rules:
- title: 2-6 words, at most 40 characters, plain language, no quotes, no emojis.
- tagline: at most 80 characters, says who it helps with what.
- description: at most 1000 characters, plain text for visitors: what the agent does, what to bring,
  what it does not do.
- soul: second person ("You are..."), 120-300 words. State who it serves, the standard workflow
  ("first... then..."), what to do when information is missing (ask one focused question), and explicit
  boundaries (what it must refuse or refer to a professional). Only from the skill content.
- greeting: at most 60 words, one line, no Markdown: a short self-introduction plus one concrete question.
- input_placeholder: at most 12 words.
- starter_questions: exactly 3, written in the visitor's voice, label at most 60 characters, each covering a
  different use of the agent.
- capabilities: 3 or 4, each with an icon from the allowed list, a name of at most 30 characters and a
  one-sentence description of at most 120 characters.
- pricing: suggest price_cents between 50 and 1000 (100 cents = 1.00 credit) and free_rounds between 0 and 5,
  with a rationale of at most 200 characters. Prefer mode "per_round".
- record_fields: 0-6 fields only when the skill naturally collects information from the visitor (for example
  a lead-intake or assessment form). Keys are lowercase snake_case. Choice fields need options. Mark personal
  data as pii.
- avatar_prompt: at most 300 characters describing a flat vector illustration of the agent persona or
  domain symbol, no text or letters.
- accent_color: one of the allowed colors that fits the domain.
- web_search_recommended: true when up-to-date public facts (rules, prices, news) help the agent.
"""


@dataclass(frozen=True)
class SkillText:
    name: str
    description: str
    body: str


def build_user_message(skills: list[SkillText]) -> str:
    """At most 16 000 chars; skill bodies share what remains after the headers equally, and a body that would
    get fewer than 200 chars is omitted."""
    headers: list[str] = []
    for i, skill in enumerate(skills, start=1):
        header = f"## Skill {i}: {clip(skill.name, NAME_MAX)}\n"
        if skill.description:
            header += f"Description: {clip(skill.description, DESCRIPTION_MAX)}\n"
        headers.append(header)
    intro = "Draft the agent listing for the following skill package(s).\n\n"
    fixed = len(intro) + sum(len(h) + len("SKILL.md:\n\n\n") for h in headers)
    per_body = (USER_MESSAGE_MAX - fixed) // max(1, len(skills))
    parts = [intro]
    for header, skill in zip(headers, skills, strict=True):
        parts.append(header)
        if per_body >= MIN_BODY_CHARS and skill.body.strip():
            parts.append("SKILL.md:\n" + clip(skill.body.strip(), per_body) + "\n")
        parts.append("\n")
    return "".join(parts)[:USER_MESSAGE_MAX]


# --------------------------------------------------------------------------- post-validation


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def _clean_title(text: str) -> str:
    return clip(_one_line(text).strip("\"'“”‘’`*# "), 40)


def _clip_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip(",;:") + "…"


def _clean_options(options: list[DraftRecordOption] | None) -> list[RecordOption]:
    seen: set[str] = set()
    out: list[RecordOption] = []
    for opt in options or []:
        key = (opt.key or "").strip().lower()
        label = _one_line(opt.label)
        if not KEY_RE.fullmatch(key) or key in seen or not label:
            continue
        seen.add(key)
        out.append(RecordOption(key=key, label=clip(label, 40)))
    return out[:30]


def clean_record_fields(fields: list[DraftRecordField]) -> list[RecordField]:
    """Drop invalid fields (lenient), then run ``records.validate_schema(lenient=True)``."""
    seen: set[str] = set()
    cleaned: list[RecordField] = []
    for f in fields:
        key = (f.key or "").strip().lower()
        label = _one_line(f.label)
        if not KEY_RE.fullmatch(key) or key in seen or not label:
            continue
        options: list[RecordOption] | None = None
        if f.type in CHOICE_TYPES:
            options = _clean_options(f.options)
            if not options:
                continue
        lo = f.min if f.type == "number" else None
        hi = f.max if f.type == "number" else None
        if lo is not None and hi is not None and lo > hi:
            lo, hi = None, None
        seen.add(key)
        cleaned.append(
            RecordField(
                key=key,
                label=clip(label, 40),
                type=f.type,
                description=clip(_one_line(f.description), 300),
                options=options,
                required=bool(f.required),
                pii=bool(f.pii),
                show_in_table=bool(f.show_in_table),
                min=lo,
                max=hi,
            )
        )
        if len(cleaned) >= 6:
            break
    if not cleaned:
        return []
    try:
        schema = deps.records().validate_schema({"fields": [c.model_dump() for c in cleaned]}, lenient=True)
    except ApiError as exc:
        log.warning("draft_record_fields_dropped code=%s", exc.code)
        return []
    except ValueError as exc:  # pydantic ValidationError from a pass-through validator
        log.warning("draft_record_fields_dropped error=%s", str(exc)[:200])
        return []
    return list(schema.fields) if schema is not None else []


def post_validate(draft: DraftModel, model_used: str) -> dict[str, Any]:
    """Clip, normalise and check a model draft → TS ``AgentDraft``; < 3 questions → 502 ``draft_failed``."""
    questions: list[StarterQuestion] = []
    for q in draft.starter_questions:
        label, prompt = _one_line(q.label), (q.prompt or "").strip()
        if label and prompt:
            questions.append(StarterQuestion(label=clip(label, 60), prompt=clip(prompt, 300)))
    if len(questions) < 3:
        raise ApiError(502, Codes.DRAFT_FAILED, "The draft was incomplete. Please try again.")
    capabilities: list[Capability] = []
    for c in draft.capabilities:
        name = _one_line(c.name)
        if not name:
            continue
        capabilities.append(
            Capability(icon=c.icon, name=clip(name, 30), description=clip(_one_line(c.description), 120))
        )
    mode: PricingMode = draft.pricing.mode if draft.pricing.mode in ("free", "per_round") else "per_round"
    price = 0 if mode == "free" else min(1000, max(50, int(draft.pricing.price_cents or 0)))
    title = _clean_title(draft.title)
    if not title:
        raise ApiError(502, Codes.DRAFT_FAILED, "The draft was incomplete. Please try again.")
    return {
        "title": title,
        "tagline": clip(_one_line(draft.tagline), 80),
        "description": clip((draft.description or "").strip(), 1000),
        "soul": clip((draft.soul or "").strip(), 6000),
        "greeting": clip(_clip_words(_one_line(draft.greeting), 60), 600),
        "input_placeholder": clip(_clip_words(_one_line(draft.input_placeholder), 12), 120),
        "starter_questions": [q.model_dump() for q in questions[:3]],
        "capabilities": [c.model_dump() for c in capabilities[:4]],
        "accent_color": draft.accent_color,
        "pricing": {
            "mode": mode,
            "price_cents": price,
            "free_rounds": min(5, max(0, int(draft.pricing.free_rounds or 0))),
            "rationale": clip(_one_line(draft.pricing.rationale), 200),
        },
        "record_fields": [f.model_dump() for f in clean_record_fields(draft.record_fields)],
        "web_search_recommended": bool(draft.web_search_recommended),
        "avatar_prompt": clip(_one_line(draft.avatar_prompt), 300),
        "model_used": model_used,
    }


# --------------------------------------------------------------------------- entry points


async def draft_from_texts(skills: list[SkillText], quality: Literal["fast", "best"] = "fast") -> dict:
    """Run the model on already-loaded skill texts (no Firestore) → TS ``AgentDraft``."""
    chain = gemini.model_chain("draft_pro" if quality == "best" else "draft")
    try:
        parsed, model_used = await gemini.generate_structured(
            chain,
            build_user_message(skills),
            DraftModel,
            system_instruction=SYSTEM_INSTRUCTION,
            thinking="low",
        )
    except ApiError as exc:
        if exc.code == Codes.RATE_LIMITED:
            raise
        raise ApiError(502, Codes.DRAFT_FAILED, "Drafting failed. Please try again.") from exc
    return post_validate(parsed, model_used)


async def draft_agent(uid: str, skill_ids: list[str], quality: Literal["fast", "best"]) -> dict:
    """``POST /api/agents/draft``: owned skills only (404), rate limited per creator (429)."""
    from topskill.skills.service import load_skill_refs, read_skill_body

    refs = await load_skill_refs(uid, skill_ids)
    await hit("draft", uid, limit=get_settings().draft_rate_limit_per_hour, window="hour")
    texts = [SkillText(name=r.name, description=r.description, body=await read_skill_body(r)) for r in refs]
    result = await draft_from_texts(texts, quality)
    log.info("draft_done uid=%s skills=%d model=%s", uid, len(refs), result["model_used"])
    return result
