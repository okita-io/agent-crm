"""Persist operator commands from the dashboard Command tab."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from agent_crm.db import session_scope, with_row_lock
from agent_crm.enums import AgencyRequestStatus
from agent_crm.models import AgencyRequest


def create_agency_request(message: str) -> AgencyRequest:
    """Queue a new operator message for the orchestrator."""
    cleaned = message.strip()
    if not cleaned:
        raise ValueError("message is required")
    now = datetime.now(UTC)
    with session_scope() as session:
        row = AgencyRequest(
            message=cleaned,
            status=AgencyRequestStatus.PENDING,
            created_at=now,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return row


def list_agency_requests(limit: int = 50) -> list[AgencyRequest]:
    """Return recent requests in chronological order."""
    limit = max(1, min(limit, 200))
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(AgencyRequest)
                .order_by(AgencyRequest.id.desc())
                .limit(limit)
            )
        )
    return list(reversed(rows))


def count_pending_agency_requests() -> int:
    with session_scope() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(AgencyRequest)
                .where(AgencyRequest.status == AgencyRequestStatus.PENDING)
            )
            or 0
        )


def claim_next_pending_agency_request() -> AgencyRequest | None:
    """Claim one pending row for the orchestrator (SKIP LOCKED on Postgres)."""
    with session_scope() as session:
        stmt = (
            select(AgencyRequest)
            .where(AgencyRequest.status == AgencyRequestStatus.PENDING)
            .order_by(AgencyRequest.id.asc())
            .limit(1)
        )
        row = session.scalar(with_row_lock(stmt, session))
        if row is None:
            return None
        row.status = AgencyRequestStatus.PROCESSING
        session.flush()
        session.refresh(row)
        return row


def mark_agency_request_completed(
    request_id: int,
    *,
    reply: str,
    actions: list[dict] | None = None,
) -> None:
    now = datetime.now(UTC)
    with session_scope() as session:
        row = session.get(AgencyRequest, request_id)
        if row is None:
            return
        row.status = AgencyRequestStatus.COMPLETED
        row.reply = reply.strip()
        row.actions = actions or []
        row.error_message = None
        row.processed_at = now


def mark_agency_request_failed(request_id: int, error_message: str) -> None:
    now = datetime.now(UTC)
    with session_scope() as session:
        row = session.get(AgencyRequest, request_id)
        if row is None:
            return
        row.status = AgencyRequestStatus.FAILED
        row.error_message = error_message.strip()[:2000]
        row.processed_at = now
