"""Engine and session management.

One place decides how to talk to the store. SQLite (dev) and Postgres (NAS)
are both first-class: swapping ``CRM_DATABASE_URL`` is the only change needed.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, get_settings
from .models import Base

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _ensure_sqlite_dir(database_url: str) -> None:
    """Create the parent directory for a file-based SQLite DB if missing."""
    if not database_url.startswith("sqlite"):
        return
    # sqlite:///./data/agent_crm.db -> ./data/agent_crm.db
    path_part = database_url.split("sqlite:///", 1)[-1]
    if path_part and path_part != ":memory:":
        Path(path_part).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _build_engine(settings: Settings) -> Engine:
    _ensure_sqlite_dir(settings.database_url)

    connect_args: dict = {}
    if settings.is_sqlite:
        # Allow use across threads (FastAPI, Streamlit) and keep the file honest.
        connect_args["check_same_thread"] = False

    engine = create_engine(
        settings.database_url,
        echo=settings.sql_echo,
        future=True,
        connect_args=connect_args,
        pool_pre_ping=not settings.is_sqlite,
    )

    if settings.is_sqlite:
        # SQLite ignores foreign keys unless asked; the model relies on them.
        @event.listens_for(engine, "connect")
        def _fk_pragma(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def get_engine(settings: Settings | None = None) -> Engine:
    """Return the process-wide engine, creating it on first use."""
    global _engine, _SessionFactory
    if _engine is None:
        settings = settings or get_settings()
        _engine = _build_engine(settings)
        _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _SessionFactory is None:
        get_engine()
    assert _SessionFactory is not None
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session: commit on success, roll back on error, always close.

    This is the unit of work the agent tooling wraps every write in.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(settings: Settings | None = None) -> None:
    """Create tables for SQLite (tests/dev). Postgres schema is via Alembic only.

    Alembic is the source of truth for schema changes on Postgres. ``create_all``
    there can fail when enums already exist (e.g. ``huntquerystatus`` from a prior
    boot). Use ``alembic upgrade head`` instead. If tables already exist but
    ``alembic_version`` is behind, ``alembic stamp <revision>`` is enough — a
    successful ``upgrade head`` does not require a follow-up stamp.
    """
    settings = settings or get_settings()
    if not settings.is_sqlite:
        return
    engine = get_engine(settings)
    Base.metadata.create_all(engine)


def reset_engine() -> None:
    """Drop cached engine/session factory. Used by tests that swap the URL."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None


def database_kind() -> str:
    """Human-readable backend name for health checks and the dashboard."""
    url = os.environ.get("CRM_DATABASE_URL", get_settings().database_url)
    scheme = urlparse(url).scheme or "unknown"
    return scheme.split("+", 1)[0]
