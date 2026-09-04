"""Persist which skill packs and modules each roster agent may use."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .agents.registry import KNOWN_AGENT_ROSTER
from .db import session_scope
from .errors import NotFoundError
from .models import AgentSkill, AgentSkillProfile
from .skill_catalog import (
    DEFAULT_AGENT_SKILLS,
    get_skill,
    sort_skill_ids,
)


def _require_known_agent(agent_name: str) -> str:
    name = agent_name.strip()
    if name not in KNOWN_AGENT_ROSTER:
        raise NotFoundError(f"unknown agent {name!r}")
    return name


def _require_known_skill(skill_id: str) -> str:
    cleaned = skill_id.strip()
    if get_skill(cleaned) is None:
        raise NotFoundError(f"unknown skill {cleaned!r}")
    return cleaned


def _ensure_initialized(session: Session, agent_name: str) -> None:
    if session.get(AgentSkillProfile, agent_name) is not None:
        return
    session.add(AgentSkillProfile(agent_name=agent_name))
    for skill_id in DEFAULT_AGENT_SKILLS.get(agent_name, ()):
        session.add(AgentSkill(agent_name=agent_name, skill_id=skill_id))


def ensure_roster_defaults() -> None:
    """Initialize skill lists for every roster agent once."""
    with session_scope() as session:
        for name in KNOWN_AGENT_ROSTER:
            _ensure_initialized(session, name)


def list_agent_skills(agent_name: str) -> list[str]:
    """Return assigned skill ids, seeding defaults the first time."""
    name = _require_known_agent(agent_name)
    with session_scope() as session:
        _ensure_initialized(session, name)
        rows = session.scalars(
            select(AgentSkill.skill_id).where(AgentSkill.agent_name == name)
        ).all()
    return sort_skill_ids(list(rows))


def list_assignments_by_agent() -> dict[str, list[str]]:
    """Map roster agent name → assigned skill ids (defaults seeded)."""
    ensure_roster_defaults()
    with session_scope() as session:
        rows = session.scalars(select(AgentSkill)).all()
    grouped: dict[str, list[str]] = {name: [] for name in KNOWN_AGENT_ROSTER}
    for row in rows:
        grouped.setdefault(row.agent_name, []).append(row.skill_id)
    return {name: sort_skill_ids(ids) for name, ids in grouped.items()}


def assign_skill(agent_name: str, skill_id: str) -> list[str]:
    """Attach ``skill_id`` to ``agent_name``. Idempotent."""
    name = _require_known_agent(agent_name)
    skill = _require_known_skill(skill_id)
    with session_scope() as session:
        _ensure_initialized(session, name)
        existing = session.get(AgentSkill, (name, skill))
        if existing is None:
            session.add(AgentSkill(agent_name=name, skill_id=skill))
        session.flush()
        rows = session.scalars(
            select(AgentSkill.skill_id).where(AgentSkill.agent_name == name)
        ).all()
    return sort_skill_ids(list(rows))


def unassign_skill(agent_name: str, skill_id: str) -> list[str]:
    """Detach ``skill_id`` from ``agent_name``. Idempotent."""
    name = _require_known_agent(agent_name)
    skill = skill_id.strip()
    with session_scope() as session:
        _ensure_initialized(session, name)
        session.execute(
            delete(AgentSkill).where(
                AgentSkill.agent_name == name,
                AgentSkill.skill_id == skill,
            )
        )
        session.flush()
        rows = session.scalars(
            select(AgentSkill.skill_id).where(AgentSkill.agent_name == name)
        ).all()
    return sort_skill_ids(list(rows))


def unassign_skill_everywhere(skill_id: str) -> int:
    """Detach ``skill_id`` from every agent. Does not delete skill files."""
    skill = _require_known_skill(skill_id)
    ensure_roster_defaults()
    with session_scope() as session:
        result = session.execute(delete(AgentSkill).where(AgentSkill.skill_id == skill))
        return int(result.rowcount or 0)


def catalog_with_usage() -> list[dict[str, object]]:
    """Catalog rows plus which roster agents currently have each skill."""
    from .skill_catalog import list_catalog

    assignments = list_assignments_by_agent()
    by_skill: dict[str, list[str]] = {}
    for agent_name, skill_ids in assignments.items():
        for skill_id in skill_ids:
            by_skill.setdefault(skill_id, []).append(agent_name)
    items: list[dict[str, object]] = []
    for record in list_catalog():
        agents = sorted(by_skill.get(record.id, []))
        items.append(
            {
                "id": record.id,
                "pack": record.pack,
                "module": record.module,
                "label": record.label,
                "summary": record.summary,
                "kind": record.kind,
                "builtin": record.builtin,
                "virtual": record.virtual,
                "agent_count": len(agents),
                "agents": agents,
            }
        )
    return items
