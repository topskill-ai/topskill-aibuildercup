from __future__ import annotations

import pytest
from pydantic import SecretStr

from topskill.config import Settings, assert_safe_runtime

pytestmark = pytest.mark.unit

PEM = SecretStr("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")


def prod_defaults() -> Settings:
    """Settings built from code defaults only (no env, no .env file)."""
    return Settings.model_construct(**{name: field.get_default(call_default_factory=True)
                                       for name, field in Settings.model_fields.items()})


def test_plain_defaults_are_prod_and_fail_closed() -> None:
    s = prod_defaults()
    assert s.env == "prod"
    assert s.auth_dev_bypass is False
    assert s.dev_bypass_active is False
    assert s.storage_backend == "gcs"
    assert s.knowledge_backend == "file_search"
    assert s.llm_mode == "live"
    assert s.firestore_project == "top-skill"
    assert s.firestore_emulator_host is None and s.firebase_auth_emulator_host is None
    assert s.model_chat == "gemini-3.8-flash"
    assert s.chat_model_allowlist == [
        "gemini-3.8-flash", "gemini-3-flash-preview", "gemini-3.1-pro-preview", "gemini-2.5-pro"
    ]


def test_settings_constructor_ignores_dotenv_and_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAT_MODEL_ALLOWLIST", "a, b ,c")
    monkeypatch.setenv("CORS_ORIGINS", "http://x.test,http://y.test")
    monkeypatch.setenv("PUBLIC_API_BASE", "http://api.test/")
    s = Settings(_env_file=None)
    assert s.chat_model_allowlist == ["a", "b", "c"]
    assert s.cors_origins == ["http://x.test", "http://y.test"]
    assert s.public_api_base == "http://api.test"


def test_prod_defaults_boot_with_mandate_key() -> None:
    s = prod_defaults().model_copy(update={"mandate_signing_key": PEM})
    assert_safe_runtime(s, {})
    assert_safe_runtime(s, {"K_SERVICE": "topskill-api"})


def test_prod_without_mandate_key_refuses() -> None:
    with pytest.raises(RuntimeError, match="MANDATE_SIGNING_KEY"):
        assert_safe_runtime(prod_defaults(), {})


def test_prod_with_bypass_refuses() -> None:
    s = prod_defaults().model_copy(update={"mandate_signing_key": PEM, "auth_dev_bypass": True})
    with pytest.raises(RuntimeError, match="AUTH_DEV_BYPASS"):
        assert_safe_runtime(s, {})


def test_dev_settings_boot_locally() -> None:
    s = prod_defaults().model_copy(update={"env": "dev", "auth_dev_bypass": True, "llm_mode": "fake"})
    assert_safe_runtime(s, {"FIRESTORE_EMULATOR_HOST": "127.0.0.1:8080"})
    assert s.dev_bypass_active is True


@pytest.mark.parametrize(
    ("update", "environ", "match"),
    [
        ({"auth_dev_bypass": True}, {}, "AUTH_DEV_BYPASS"),
        ({"env": "dev"}, {}, "ENV=dev"),
        ({"env": "test"}, {}, "ENV=test"),
        ({"llm_mode": "fake"}, {}, "LLM_MODE"),
        ({}, {"FIRESTORE_EMULATOR_HOST": "127.0.0.1:8080"}, "FIRESTORE_EMULATOR_HOST"),
        ({}, {"FIREBASE_AUTH_EMULATOR_HOST": "127.0.0.1:9099"}, "FIREBASE_AUTH_EMULATOR_HOST"),
    ],
)
def test_cloud_run_refuses_dev_settings(update: dict, environ: dict, match: str) -> None:
    s = prod_defaults().model_copy(update={"mandate_signing_key": PEM, **update})
    with pytest.raises(RuntimeError, match=match):
        assert_safe_runtime(s, {"K_SERVICE": "topskill-api", **environ})


def test_bypass_inactive_in_prod_even_if_set() -> None:
    s = prod_defaults().model_copy(update={"auth_dev_bypass": True})
    assert s.dev_bypass_active is False
