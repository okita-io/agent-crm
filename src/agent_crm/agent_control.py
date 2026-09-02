"""Persist Live Agents on/off switches and pause standing workers."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from sqlalchemy import select

from .db import session_scope
from .models import AgentToggle

logger = logging.getLogger(__name__)

PAUSED_TASK = "paused"
_POLL_SECONDS = 5.0

# Standing workers that receive assigned work (excludes the orchestrator itself).
WORK_AGENTS: tuple[str, ...] = (
    "outbound_hunter",
    "research",
    "engagement",
    "seo",
    "aeo-geo",
    "queue-review",
    "job-dispatcher",
)

DISPATCHER_AGENT = "job-dispatcher"

# Agency command enqueue types → the standing agent that drains them.
ENQUEUE_ACTION_AGENTS: dict[str, str] = {
    "enqueue_hunt": "outbound_hunter",
    "enqueue_research": "research",
    "enqueue_engagement": "engagement",
    "enqueue_seo": "seo",
    "enqueue_aeo_geo": "aeo-geo",
}


def is_agent_enabled(agent_name: str) -> bool:
    """Return True when the agent may work. Missing rows default to on."""
    with session_scope() as session:
        row = session.get(AgentToggle, agent_name)
        if row is None:
            return True
        return bool(row.enabled)


def list_agent_enabled() -> dict[str, bool]:
    """Return stored switch values. Agents with no row are omitted (treat as on)."""
    with session_scope() as session:
        rows = session.scalars(select(AgentToggle)).all()
        return {row.agent_name: bool(row.enabled) for row in rows}


def roster_enabled(names: tuple[str, ...] | list[str] | None = None) -> dict[str, bool]:
    """Return on/off for each name. Missing toggle rows default to on."""
    stored = list_agent_enabled()
    keys = tuple(names) if names is not None else WORK_AGENTS
    return {name: stored.get(name, True) for name in keys}


def enabled_work_agents() -> list[str]:
    """Standing work agents whose Live Agents switch is on."""
    return [name for name, on in roster_enabled().items() if on]


def is_focused_roster(enabled: list[str] | None = None) -> bool:
    """True when only one or two work agents are switched on."""
    names = enabled if enabled is not None else enabled_work_agents()
    return 1 <= len(names) <= 2


def allowed_enqueue_actions(enabled: list[str] | None = None) -> list[str]:
    """Enqueue action types that currently enabled agents can drain."""
    on = set(enabled if enabled is not None else enabled_work_agents())
    return [
        action
        for action, agent in ENQUEUE_ACTION_AGENTS.items()
        if agent in on
    ]


def dispatcher_work_allowed() -> bool:
    """True when the job dispatcher may be assigned verify/enrich/qualify work."""
    return is_agent_enabled(DISPATCHER_AGENT)


def set_agent_enabled(agent_name: str, enabled: bool) -> bool:
    """Upsert the switch and, when turning off, mark the heartbeat paused."""
    name = agent_name.strip()
    if not name:
        raise ValueError("agent_name is required")
    now = datetime.now(UTC)
    with session_scope() as session:
        row = session.get(AgentToggle, name)
        if row is None:
            row = AgentToggle(agent_name=name, enabled=enabled, updated_at=now)
            session.add(row)
        else:
            row.enabled = enabled
            row.updated_at = now
        session.flush()
        stored = bool(row.enabled)
    if not stored:
        _mark_paused(name)
    return stored


def stop_if_disabled(agent_name: str) -> bool:
    """Return True when the agent is switched off (caller should stop this cycle)."""
    if is_agent_enabled(agent_name):
        return False
    _mark_paused(agent_name)
    return True


def wait_while_disabled(
    agent_name: str,
    *,
    poll_seconds: float = _POLL_SECONDS,
) -> None:
    """Block until the Live Agents switch is on. Heartbeats stay ``paused``."""
    interval = max(1.0, float(poll_seconds))
    announced = False
    while stop_if_disabled(agent_name):
        if not announced:
            logger.info("%s paused; waiting for Live Agents switch", agent_name)
            announced = True
        time.sleep(interval)


def _mark_paused(agent_name: str) -> None:
    from .enums import AgentStatus
    from .heartbeat import record_heartbeat

    record_heartbeat(agent_name, status=AgentStatus.IDLE, task=PAUSED_TASK)
