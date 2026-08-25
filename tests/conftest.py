"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    """Isolated SQLite database per test."""
    path = tmp_path / "test.db"
    url = f"sqlite:///{path}"
    monkeypatch.setenv("CRM_DATABASE_URL", url)
    monkeypatch.setenv("CRM_HUNTER_ENABLE_LLM", "false")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield url
    reset_engine()
    get_settings.cache_clear()

