"""Helpers for the live File Search tests: the Developer API key comes from Secret Manager."""

from __future__ import annotations

import os
import subprocess

import pytest


def gemini_api_key() -> str:
    """``GEMINI_API_KEY`` from the env, else Secret Manager ``gemini-api-key``; skips the test if neither."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    try:
        out = subprocess.run(
            [
                "gcloud",
                "secrets",
                "versions",
                "access",
                "latest",
                "--secret=gemini-api-key",
                "--project=top-skill",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("GEMINI_API_KEY unavailable (env or Secret Manager)")
    key = out.stdout.strip()
    if not key:
        pytest.skip("GEMINI_API_KEY is empty")
    return key
