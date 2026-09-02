"""Persistence for self-learning orchestration notes."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy import select

from .db import session_scope
from .enums import (
    ImprovementNoteKind,
    ImprovementNoteSeverity,
    ImprovementNoteStatus,
    ImprovementSourceAgent,
)
from .models import AgentImprovementNote
from .schemas import ImprovementNoteOut

logger = logging.getLogger(__name__)


def make_fingerprint(*parts: str) -> str:
    """Build a stable fingerprint from note parts."""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:64]


def record_improvement_note(
    *,
    kind: ImprovementNoteKind,
    severity: ImprovementNoteSeverity,
    source_agent: ImprovementSourceAgent,
    title: str,
    body: str,
    fingerprint: str,
    metrics: dict[str, Any] | None = None,
    suggested_fix: str | None = None,
) -> int | None:
    """Insert or refresh an open improvement note (deduped by fingerprint)."""
    normalized_fp = fingerprint.strip()[:512]
    if not normalized_fp:
        normalized_fp = make_fingerprint(source_agent.value, kind.value, title)

    with session_scope() as session:
        existing = session.scalar(
            select(AgentImprovementNote)
            .where(AgentImprovementNote.fingerprint == normalized_fp)
            .where(AgentImprovementNote.status == ImprovementNoteStatus.OPEN)
            .limit(1)
        )
        if existing is not None:
            existing.title = title[:255]
            existing.body = body
            existing.metrics = metrics
            existing.suggested_fix = suggested_fix
            existing.severity = severity
            existing.kind = kind
            existing.source_agent = source_agent
            session.flush()
            return existing.id

        row = AgentImprovementNote(
            kind=kind,
            severity=severity,
            source_agent=source_agent,
            title=title[:255],
            body=body,
            metrics=metrics,
            suggested_fix=suggested_fix,
            status=ImprovementNoteStatus.OPEN,
            fingerprint=normalized_fp,
        )
        session.add(row)
        session.flush()
        return row.id


def list_improvement_notes(
    *,
    status: ImprovementNoteStatus | None = ImprovementNoteStatus.OPEN,
    limit: int | None = 200,
) -> list[ImprovementNoteOut]:
    """List improvement notes, newest first."""
    with session_scope() as session:
        stmt = select(AgentImprovementNote).order_by(
            AgentImprovementNote.created_at.desc(),
            AgentImprovementNote.id.desc(),
        )
        if status is not None:
            stmt = stmt.where(AgentImprovementNote.status == status)
        if limit is not None:
            stmt = stmt.limit(max(limit, 1))
        rows = list(session.scalars(stmt))
        return [ImprovementNoteOut.model_validate(row) for row in rows]


def count_open_improvement_notes() -> int:
    with session_scope() as session:
        stmt = (
            select(AgentImprovementNote)
            .where(AgentImprovementNote.status == ImprovementNoteStatus.OPEN)
        )
        return len(list(session.scalars(stmt)))
