"""Model Armor REST backend (SPEC §18 "Guard").

Calls the **regional** endpoint ``https://modelarmor.{location}.rep.googleapis.com`` (the global endpoint
does not serve regional templates) with ADC credentials (``roles/modelarmor.user`` on the runtime service
account). No extra client library: one ``httpx`` POST per screened text.

Verified shapes (template ``topskill-public`` in asia-southeast1, 2026-10-15)::

    {"sanitizationResult": {
       "filterMatchState": "MATCH_FOUND",
       "filterResults": {
         "rai": {"raiFilterResult": {"matchState": ..., "raiFilterTypeResults":
                 {"dangerous": {"matchState": "MATCH_FOUND", "confidenceLevel": "HIGH"}, ...}}},
         "pi_and_jailbreak": {"piAndJailbreakFilterResult": {"matchState": "MATCH_FOUND", ...}},
         "sdp": {"sdpFilterResult": {"inspectResult": {"matchState": "MATCH_FOUND", "findings": [
                 {"infoType": "CREDIT_CARD_NUMBER", "likelihood": "VERY_LIKELY",
                  "location": {"byteRange": {...}, "codepointRange": {"start": "18", "end": "37"}}}]}}}},
       "invocationResult": "SUCCESS"}}

``malicious_uris`` is not available in asia-southeast1 (``CAPABILITY_NOT_SUPPORTED``), so the template has
no URI filter; ``csam`` results are parsed when present.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import google.auth
import google.auth.transport.requests
import httpx

from topskill.guard.types import Finding, ScreenKind, ScreenResult

log = logging.getLogger(__name__)

SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)
MATCH = "MATCH_FOUND"


class GuardUnavailable(Exception):
    """Model Armor could not give a verdict (HTTP error, bad payload, auth failure)."""


def endpoint(location: str) -> str:
    return f"https://modelarmor.{location}.rep.googleapis.com"


def template_name(project: str, location: str, template: str) -> str:
    return f"projects/{project}/locations/{location}/templates/{template}"


def _inner(wrapper: Any) -> dict:
    """``{"raiFilterResult": {...}}`` → the inner dict."""
    if isinstance(wrapper, dict):
        for value in wrapper.values():
            if isinstance(value, dict):
                return value
    return {}


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_sanitization(data: Any) -> ScreenResult:
    """REST response → :class:`ScreenResult`. Raises :class:`GuardUnavailable` on an unusable payload."""
    if not isinstance(data, dict) or not isinstance(data.get("sanitizationResult"), dict):
        raise GuardUnavailable("response has no sanitizationResult")
    result = data["sanitizationResult"]
    filters = result.get("filterResults", {})
    if not isinstance(filters, dict):
        raise GuardUnavailable("filterResults is not an object")
    categories: list[str] = []
    findings: list[Finding] = []
    sanitized: str | None = None
    for key, wrapper in filters.items():
        inner = _inner(wrapper)
        if key == "rai":
            types_ = inner.get("raiFilterTypeResults") or {}
            hits = [t for t, r in types_.items() if isinstance(r, dict) and r.get("matchState") == MATCH]
            categories.extend(f"rai:{t}" for t in sorted(hits))
            if not hits and inner.get("matchState") == MATCH:
                categories.append("rai")
        elif key == "sdp":
            inspect = inner.get("inspectResult") or {}
            deid = inner.get("deidentifyResult") or {}
            if inspect.get("matchState") == MATCH or deid.get("matchState") == MATCH:
                categories.append("sdp")
            for f in inspect.get("findings") or []:
                rng = ((f or {}).get("location") or {}).get("codepointRange") or {}
                start, end = _int(rng.get("start", 0)), _int(rng.get("end"))
                if start is not None and end is not None and end > start:
                    findings.append(Finding(str(f.get("infoType") or ""), start, end))
            text = ((deid.get("data") or {}).get("text")) if deid.get("matchState") == MATCH else None
            if isinstance(text, str):
                sanitized = text
        elif inner.get("matchState") == MATCH:
            categories.append(key)
    if not categories and result.get("filterMatchState") == MATCH:
        log.warning("guard_unparsed_match filters=%s", sorted(filters))
    return ScreenResult(categories=tuple(categories), findings=tuple(findings), sanitized_text=sanitized)


class ModelArmorBackend:
    """``sanitizeUserPrompt`` / ``sanitizeModelResponse`` on one template."""

    def __init__(self, *, project: str, location: str, template: str, timeout_s: float) -> None:
        self.base = f"{endpoint(location)}/v1/{template_name(project, location, template)}"
        self.timeout_s = timeout_s
        self._credentials: Any = None
        self._auth_lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None

    async def _headers(self) -> dict[str, str]:
        async with self._auth_lock:
            if self._credentials is None:
                self._credentials, _ = await asyncio.to_thread(google.auth.default, scopes=list(SCOPES))
            creds = self._credentials
            if not creds.valid:
                await asyncio.to_thread(creds.refresh, google.auth.transport.requests.Request())
            headers = {"Content-Type": "application/json"}
            creds.apply(headers)  # Authorization (+ x-goog-user-project for user ADC with a quota project)
            return headers

    async def warm_up(self) -> None:
        """Load credentials and a token now, and open a pooled connection with a GET of the API root
        (any status is fine; nothing is screened)."""
        await self._headers()
        try:
            await self._http().get(self.base.split("/v1/")[0] + "/", timeout=self.timeout_s)
        except httpx.HTTPError:
            pass

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    async def screen(self, kind: ScreenKind, text: str) -> ScreenResult:
        if kind == "prompt":
            url, body = f"{self.base}:sanitizeUserPrompt", {"userPromptData": {"text": text}}
        else:
            url, body = f"{self.base}:sanitizeModelResponse", {"modelResponseData": {"text": text}}
        try:
            resp = await self._http().post(url, json=body, headers=await self._headers())
        except httpx.HTTPError as exc:
            raise GuardUnavailable(f"transport: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise GuardUnavailable(f"HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise GuardUnavailable("response is not JSON") from exc
        return parse_sanitization(data)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
