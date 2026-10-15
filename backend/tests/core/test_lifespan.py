from __future__ import annotations

import pytest

from tests.core.helpers import settings_env
from topskill.main import create_app


@pytest.mark.unit
async def test_lifespan_refuses_dev_settings_on_cloud_run(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    monkeypatch.setenv("K_SERVICE", "topskill-api")
    with pytest.raises(RuntimeError, match="Cloud Run"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.unit
async def test_lifespan_refuses_prod_with_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    with settings_env(monkeypatch, env="prod", mandate_signing_key="pem"):
        app = create_app()
        with pytest.raises(RuntimeError, match="AUTH_DEV_BYPASS"):
            async with app.router.lifespan_context(app):
                pass


@pytest.mark.emulator
async def test_lifespan_starts_and_drains_in_test_env() -> None:
    app = create_app()
    async with app.router.lifespan_context(app):
        pass
