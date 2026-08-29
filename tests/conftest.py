"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine


@pytest.fixture(autouse=True)
def _disable_api_token_by_default(monkeypatch):
    """Tests assume open auth unless they set ``CRM_API_TOKEN`` themselves.

    ``Settings`` also reads ``.env``, so deleting the env var is not enough.
    """
    monkeypatch.setenv("CRM_API_TOKEN", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    """Isolated SQLite database per test."""
    path = tmp_path / "test.db"
    url = f"sqlite:///{path}"
    monkeypatch.setenv("CRM_DATABASE_URL", url)
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield url
    reset_engine()
    get_settings.cache_clear()
