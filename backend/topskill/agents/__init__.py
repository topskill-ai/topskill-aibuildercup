"""Agents package [publish]: drafting, avatars, publishing, versions and public pages (SPEC §8.7, §8.8).

Cross-package contract (SPEC §14.2): ``load_skill_refs``, ``read_skill_file``, ``resolve_starter_questions``,
``avatar_url``, ``agent_urls``.
"""

from topskill.agents.schemas import agent_urls, avatar_url, resolve_starter_questions
from topskill.skills.service import load_skill_refs, read_skill_file

__all__ = ["agent_urls", "avatar_url", "load_skill_refs", "read_skill_file", "resolve_starter_questions"]
