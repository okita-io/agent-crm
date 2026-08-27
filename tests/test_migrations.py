"""Alembic migration tests."""

from __future__ import annotations

import os
from unittest.mock import patch
from urllib.parse import urlparse, urlunparse

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.models import Base

DEFAULT_TEST_POSTGRES_URL = "postgresql+psycopg://crm:crm@localhost:5432/postgres"
RESEARCH_FINDINGS_REVISION = "b2c3d4e5f6a7"
PRE_TOPICAL_RELEVANCE_REVISION = "j5k6l7m8n9o0"


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


@pytest.fixture()
def postgres_migration_db_at_j5(monkeypatch):
    """Fresh Postgres database migrated through qualify_contact (pre-topical)."""
    admin_url = _postgres_admin_url()
    if admin_url is None:
        pytest.skip("Postgres is not available for migration tests")

    db_name = "agent_crm_migration_k6_test"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))

    db_url = _database_url(admin_url, db_name)
    monkeypatch.setenv("CRM_DATABASE_URL", db_url)
    get_settings.cache_clear()
    reset_engine()

    cfg = Config("alembic.ini")
    command.upgrade(cfg, PRE_TOPICAL_RELEVANCE_REVISION)

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


def test_k6_upgrade_when_enum_exists_without_table(
    postgres_migration_db_at_j5, monkeypatch
) -> None:
    """Simulate create_all racing Alembic: enum exists, table missing."""
    db_url = postgres_migration_db_at_j5
    monkeypatch.setenv("CRM_DATABASE_URL", db_url)
    get_settings.cache_clear()
    reset_engine()

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TYPE topicalrelevanceverdict AS ENUM "
                    "('on_topic', 'off_topic', 'uncertain')"
                )
            )

        with engine.connect() as conn:
            table_rows = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'url_topic_relevance'"
                )
            ).fetchall()
            assert table_rows == []

        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")

        inspector = inspect(engine)
        assert "url_topic_relevance" in inspector.get_table_names()

        with engine.connect() as conn:
            enum_rows = conn.execute(
                text("SELECT 1 FROM pg_type WHERE typname = 'topicalrelevanceverdict'")
            ).fetchall()
            assert len(enum_rows) == 1
    finally:
        engine.dispose()


def test_init_db_does_not_create_all_on_postgres(monkeypatch) -> None:
    monkeypatch.setenv(
        "CRM_DATABASE_URL",
        "postgresql+psycopg://crm:crm@localhost:5432/agent_crm",
    )
    get_settings.cache_clear()
    reset_engine()

    with patch.object(Base.metadata, "create_all") as create_all:
        init_db()
        create_all.assert_not_called()

    reset_engine()
    get_settings.cache_clear()
