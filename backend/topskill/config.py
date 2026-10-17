"""Application settings.

Every code default is the production value (fail closed, SPEC §4.1). Development values live only in
``backend/.env.example`` (copied to the git-ignored ``backend/.env``), ``deploy/scripts/dev_up.sh`` and
``tests/conftest.py``. A process started with no env file boots as prod with the auth bypass off and no
emulators, and on Cloud Run the boot guard (:func:`assert_safe_runtime`) refuses any dev setting.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

log = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent

CsvList = Annotated[list[str], NoDecode]


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- runtime identity
    env: Literal["dev", "test", "prod"] = "prod"
    app_version: str = "unknown"
    log_level: str = "INFO"

    # ---- Google Cloud
    google_cloud_project: str = "top-skill"
    firestore_project: str = "top-skill"
    google_cloud_location: str = "global"
    google_genai_use_vertexai: bool = True
    firestore_emulator_host: str | None = None
    firebase_auth_emulator_host: str | None = None

    # ---- auth
    auth_dev_bypass: bool = False

    # ---- storage
    storage_backend: Literal["gcs", "local"] = "gcs"
    gcs_bucket: str = "top-skill-files"
    local_storage_dir: str = ".data/blobs"

    # ---- knowledge (D1: Gemini File Search via the Developer API in prod, local backend in dev/test)
    knowledge_backend: Literal["file_search", "local"] = "file_search"
    gemini_api_key: SecretStr | None = None
    model_file_search: str = "gemini-3.1-flash-lite"
    model_file_search_fallback: str = "gemini-3.8-flash"
    rag_top_k: int = Field(4, ge=1, le=20)

    # ---- models (D6)
    model_chat: str = "gemini-3.8-flash"
    model_chat_fallback: str = "gemini-3-flash-preview"
    model_draft: str = "gemini-3.8-flash"
    model_draft_fallback: str = "gemini-3-flash-preview"
    model_draft_pro: str = "gemini-3.1-pro-preview"
    model_draft_pro_fallback: str = "gemini-2.5-pro"
    model_extract: str = "gemini-3.8-flash"
    model_extract_fallback: str = "gemini-3-flash-preview"
    model_assistant: str = "gemini-3.8-flash"
    model_assistant_fallback: str = "gemini-3-flash-preview"
    model_image: str = "gemini-3.1-flash-image"
    model_image_fallback: str = "gemini-2.5-flash-image"
    chat_model_allowlist: CsvList = Field(
        default_factory=lambda: [
            "gemini-3.8-flash",
            "gemini-3-flash-preview",
            "gemini-3.1-pro-preview",
            "gemini-2.5-pro",
        ]
    )
    llm_mode: Literal["live", "fake"] = "live"

    # ---- billing
    creator_revenue_share_bp: int = Field(8000, ge=0, le=10000)
    signup_grant_cents: int = Field(10000, ge=0)
    demo_topup_cents: int = Field(5000, ge=0)
    demo_topup_daily_limit: int = Field(5, ge=0)

    # ---- public URLs and CORS
    public_api_base: str = "https://topskill-api-920441209758.asia-southeast1.run.app"
    public_web_base: str = "https://top-skill.web.app"
    cors_origins: CsvList = Field(
        default_factory=lambda: [
            "https://www.openskill.top",
            "https://top-skill.web.app",
            "https://top-skill.firebaseapp.com",
            "http://localhost:5173",
        ]
    )
    cors_origin_regex: str = r"^https://top-skill--[a-z0-9-]+\.web\.app$"

    # ---- mandates (A2A)
    mandate_signing_key: SecretStr | None = None
    mandate_key_file: str | None = None
    mandate_kid: str = "tsk-2026-10"
    mandate_max_ttl_minutes: int = Field(1440, ge=1)
    mandate_max_budget_cents: int = Field(100_000, ge=1)
    mandate_default_max_amount_cents: int = Field(500, ge=1)
    mandate_default_budget_cents: int = Field(2000, ge=1)
    mandate_default_ttl_minutes: int = Field(30, ge=1)

    # ---- turn limits
    turn_idle_timeout_s: int = Field(120, ge=1)
    turn_total_timeout_s: int = Field(300, ge=1)
    max_concurrent_turns: int = Field(40, ge=1)
    max_rounds_per_conversation: int = Field(30, ge=1)
    max_llm_calls_per_turn: int = Field(8, ge=1)
    draft_rate_limit_per_hour: int = Field(30, ge=0)
    avatar_rate_limit_per_hour: int = Field(20, ge=0)
    a2a_call_timeout_s: int = Field(120, ge=1)

    # ---- request limits
    max_request_bytes: int = Field(30 * 1024 * 1024, ge=1)

    # ---- voice mode (P2, SPEC §18.1): Gemini Live over a browser WebSocket. Models probed on Vertex for
    # project top-skill on 2026-10-15: gemini-live-2.5-flash answers on `global`;
    # gemini-live-2.5-flash-native-audio only on `us-central1`.
    voice_enabled: bool = True
    voice_model: str = "gemini-live-2.5-flash"
    voice_location: str = "global"
    voice_model_fallback: str = "gemini-live-2.5-flash-native-audio"
    voice_location_fallback: str = "us-central1"
    # Vertex ends a Live audio session after ~10 min; the limit plus the 20 s answer grace stays inside it.
    voice_max_session_s: int = Field(540, ge=1, le=570)
    voice_idle_timeout_s: int = Field(90, ge=1)
    voice_auth_timeout_s: int = Field(10, ge=1)
    voice_max_sessions: int = Field(20, ge=1)
    voice_sessions_per_hour: int = Field(30, ge=0)
    voice_connect_timeout_s: float = Field(12.0, gt=0)  # per Live plan: WS handshake + setupComplete
    voice_max_tool_calls_per_round: int = Field(8, ge=1)

    # ---- guard: Model Armor screening of public agents (P2, SPEC §18.2); fail-open on errors
    guard_backend: Literal["model_armor", "off"] = "model_armor"
    guard_project: str = "top-skill"
    guard_location: str = "asia-southeast1"
    guard_template: str = "topskill-public"
    guard_timeout_s: float = Field(3.0, gt=0, le=30)
    guard_max_chars: int = Field(20_000, ge=100, le=200_000)
    guard_prompt_block_filters: CsvList = Field(
        default_factory=lambda: ["pi_and_jailbreak", "rai", "malicious_uris", "csam"]
    )
    guard_response_block_filters: CsvList = Field(default_factory=lambda: ["csam"])
    guard_redact_sdp: bool = True

    # ---- tracing (P2, SPEC §18.3): OpenTelemetry → Cloud Trace; tests and .env.example turn it off
    tracing_enabled: bool = True
    trace_otlp_endpoint: str = "https://telemetry.googleapis.com/v1/traces"
    trace_sample_ratio_turns: float = Field(1.0, ge=0.0, le=1.0)  # chat / a2a / assistant / voice routes
    trace_sample_ratio: float = Field(1.0, ge=0.0, le=1.0)  # every other route and background work
    trace_sample_ratio_health: float = Field(0.01, ge=0.0, le=1.0)  # /health, /healthz, meta, OPTIONS
    trace_capture_content: bool = False  # ADK prompt/response bodies in spans (visitor text = PII)

    # ---- analytics: "Ask your data" over BigQuery (P2, SPEC §18). Off unless enabled; outside prod the
    # prod dataset is refused (use topskill_analytics_dev).
    analytics_enabled: bool = False
    analytics_project: str = ""  # "" = GOOGLE_CLOUD_PROJECT
    analytics_dataset: str = "topskill_analytics"
    analytics_location: str = "asia-southeast1"
    analytics_max_bytes: int = Field(100 * 1024 * 1024, ge=1)  # dry-run cap and maximum_bytes_billed
    analytics_query_timeout_s: int = Field(20, ge=1, le=120)
    analytics_max_rows: int = Field(200, ge=1, le=5000)
    analytics_ask_per_hour: int = Field(30, ge=0)
    analytics_sync_min_interval_s: int = Field(60, ge=0)
    analytics_sync_wait_s: int = Field(5, ge=0, le=120)  # the scheduler already keeps data ≤ 15 min old
    analytics_sync_invokers: CsvList = Field(default_factory=list)  # OIDC emails allowed to call the sync
    analytics_sync_audience: str = ""  # "" = PUBLIC_API_BASE
    model_analytics: str = "gemini-3.8-flash"
    model_analytics_fallback: str = "gemini-3-flash-preview"
    analytics_plan_timeout_s: int = Field(25, ge=1, le=300)  # one SQL-writing model call
    analytics_answer_timeout_s: int = Field(15, ge=1, le=300)  # summary call; on timeout: plain sentence
    analytics_ask_timeout_s: int = Field(60, ge=1, le=600)  # whole question → 503 service_unavailable

    _split_analytics_lists = field_validator("analytics_sync_invokers", mode="before")(_split_csv)

    _split_lists = field_validator("chat_model_allowlist", "cors_origins", mode="before")(_split_csv)
    _split_guard_lists = field_validator(
        "guard_prompt_block_filters", "guard_response_block_filters", mode="before"
    )(_split_csv)

    @field_validator("public_api_base", "public_web_base")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator(
        "firestore_emulator_host", "firebase_auth_emulator_host", "mandate_key_file", mode="before"
    )
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _check(self) -> Settings:
        if self.mandate_default_budget_cents < self.mandate_default_max_amount_cents:
            raise ValueError("mandate_default_budget_cents must be >= mandate_default_max_amount_cents")
        return self

    # ---- derived helpers
    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def dev_bypass_active(self) -> bool:
        """The dev auth bypass is honoured only outside prod (SPEC §4.2)."""
        return self.auth_dev_bypass and self.env in ("dev", "test")

    @property
    def local_storage_path(self) -> Path:
        path = Path(self.local_storage_dir)
        return path if path.is_absolute() else BACKEND_DIR / path

    @property
    def mandate_key_path(self) -> Path | None:
        if not self.mandate_key_file:
            return None
        path = Path(self.mandate_key_file)
        return path if path.is_absolute() else BACKEND_DIR / path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def assert_safe_runtime(settings: Settings, environ: Mapping[str, str]) -> None:
    """Boot guard (SPEC §3.2). Raises ``RuntimeError`` so the container exits instead of serving."""
    problems: list[str] = []
    if settings.env == "prod" and settings.auth_dev_bypass:
        problems.append("AUTH_DEV_BYPASS must be false when ENV=prod")
    if environ.get("K_SERVICE"):
        if settings.auth_dev_bypass:
            problems.append("AUTH_DEV_BYPASS is set on Cloud Run")
        if settings.env != "prod":
            problems.append(f"ENV={settings.env} on Cloud Run (must be prod)")
        if settings.llm_mode != "live":
            problems.append(f"LLM_MODE={settings.llm_mode} on Cloud Run (must be live)")
        for var in ("FIRESTORE_EMULATOR_HOST", "FIREBASE_AUTH_EMULATOR_HOST"):
            if environ.get(var):
                problems.append(f"{var} is set on Cloud Run")
    if settings.env == "prod" and not (
        settings.mandate_signing_key and settings.mandate_signing_key.get_secret_value().strip()
    ):
        problems.append("MANDATE_SIGNING_KEY is required when ENV=prod")
    if problems:
        raise RuntimeError("Unsafe runtime configuration: " + "; ".join(problems))
    if settings.env == "prod" and settings.knowledge_backend == "file_search" and not settings.gemini_api_key:
        log.warning(
            "KNOWLEDGE_BACKEND=file_search but GEMINI_API_KEY is not set; knowledge ingestion will fail"
        )
