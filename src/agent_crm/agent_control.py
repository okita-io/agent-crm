"""Per-agent enable/disable for standing worker loops."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .db import session_scope
from .enums import AgentStatus
from .heartbeat import record_heartbeat
from .models import AgentToggle
from .presence import KNOWN_AGENT_ROSTER

POLL_SECONDS = 5.0


def is_agent_enabled(agent_name: str) -> bool:
    """Return whether an agent may run. Missing rows default to enabled."""
    try:
        with session_scope() as session:
            row = session.get(AgentToggle, agent_name)
            if row is None:
                return True
            return bool(row.enabled)
    except SQLAlchemyError:
        return True


def list_agent_enabled() -> dict[str, bool]:
    """Return enabled state for roster agents plus any stored toggle rows."""
    stored: dict[str, bool] = {}
    try:
        with session_scope() as session:
            rows = session.scalars(select(AgentToggle)).all()
            stored = {row.agent_name: bool(row.enabled) for row in rows}
    except SQLAlchemyError:
        stored = {}

    result = {name: stored.get(name, True) for name in KNOWN_AGENT_ROSTER}
    for agent_name, enabled in stored.items():
        result.setdefault(agent_name, enabled)
    return result


def set_agent_enabled(agent_name: str, enabled: bool) -> bool:
    """Upsert toggle state. When disabled, heartbeat task becomes ``paused``."""
    now = datetime.now(UTC)
    with session_scope() as session:
        row = session.get(AgentToggle, agent_name)
        if row is None:
            row = AgentToggle(agent_name=agent_name, enabled=enabled, updated_at=now)
            session.add(row)
        else:
            row.enabled = enabled
            row.updated_at = now
        session.flush()

    if not enabled:
        record_heartbeat(agent_name, status=AgentStatus.IDLE, task="paused")
    return enabled


def stop_if_disabled(agent_name: str) -> bool:
    """Return True when the agent is paused and the current cycle should stop."""
    if is_agent_enabled(agent_name):
        return False
    record_heartbeat(agent_name, status=AgentStatus.IDLE, task="paused")
    return True


def wait_while_disabled(agent_name: str, *, poll_seconds: float = POLL_SECONDS) -> None:
    """Block until the agent is enabled again."""
    while not is_agent_enabled(agent_name):
        record_heartbeat(agent_name, status=AgentStatus.IDLE, task="paused")
        time.sleep(poll_seconds)
