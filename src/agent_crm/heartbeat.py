"""Agent heartbeat persistence for the live observer."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from .db import session_scope
from .enums import AgentStatus
from .models import AgentHeartbeat
from .presence import HeartbeatSnapshot


def record_heartbeat(
    agent_name: str,
    *,
    status: AgentStatus,
    task: str | None = None,
    resource: str | None = None,
    metadata: dict | None = None,
) -> HeartbeatSnapshot:
    """Upsert the latest heartbeat for an agent actor."""
    now = datetime.now(UTC)
    with session_scope() as session:
        row = session.get(AgentHeartbeat, agent_name)
        if row is None:
            row = AgentHeartbeat(agent_name=agent_name)
            session.add(row)
        row.status = status
        row.task = task
        row.resource = resource
        row.metadata_ = metadata
        row.last_seen_at = now
        session.flush()
        return HeartbeatSnapshot(
            agent_name=row.agent_name,
            status=row.status,
            task=row.task,
            resource=row.resource,
            last_seen_at=row.last_seen_at,
        )


def list_heartbeats() -> list[HeartbeatSnapshot]:
    """Return every stored heartbeat."""
    with session_scope() as session:
        rows = session.scalars(select(AgentHeartbeat).order_by(AgentHeartbeat.agent_name)).all()
        return [
            HeartbeatSnapshot(
                agent_name=row.agent_name,
                status=row.status,
                task=row.task,
                resource=row.resource,
                last_seen_at=row.last_seen_at,
            )
            for row in rows
        ]
