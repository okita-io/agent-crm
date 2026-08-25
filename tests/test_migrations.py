"""Alembic migration tests."""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from agent_crm.config import get_settings
from agent_crm.db import reset_engine

DEFAULT_TEST_POSTGRES_URL = "postgresql+psycopg://crm:crm@localhost:5432/postgres"
RESEARCH_FINDINGS_REVISION = "b2c3d4e5f6a7"


def _postgres_admin_url() -> str | None:
    """Return a reachable Postgres admin URL, or None when Postgres is unavailable."""
    url = os.environ.get("TEST_POSTGRES_URL", DEFAULT_TEST_POSTGRES_URL)
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return None
    finally:
        engine.dispose()
    return url


def _database_url(admin_url: str, db_name: str) -> str:
    parsed = urlparse(admin_url)
    path = f"/{db_name}"
    return urlunparse(parsed._replace(path=path))


@pytest.fixture()
def postgres_migration_db(monkeypatch):
    """Fresh Postgres database migrated through research_findings."""
    admin_url = _postgres_admin_url()
    if admin_url is None:
        pytest.skip("Postgres is not available for migration tests")

    db_name = "agent_crm_migration_test"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))

    db_url = _database_url(admin_url, db_name)
    monkeypatch.setenv("CRM_DATABASE_URL", db_url)
    get_settings.cache_clear()
    reset_engine()

    cfg = Config("alembic.ini")
    command.upgrade(cfg, RESEARCH_FINDINGS_REVISION)

    yield db_url

    reset_engine()
    get_settings.cache_clear()
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    admin_engine.dispose()


def test_research_findings_migration_creates_table(postgres_migration_db) -> None:
    """b2c3d4e5f6a7 must create research_findings without duplicate enum DDL."""
    engine = create_engine(postgres_migration_db)
    try:
        inspector = inspect(engine)
        assert "research_findings" in inspector.get_table_names()

        with engine.connect() as conn:
            enum_rows = conn.execute(
                text("SELECT 1 FROM pg_type WHERE typname = 'researchfindingkind'")
            ).fetchall()
            assert len(enum_rows) == 1
    finally:
        engine.dispose()
