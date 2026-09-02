"""Append-only persistence for the engagement search-term queue.

Rows are never deleted. Completing a query changes status only; discovered
follow-up terms are inserted if they are new.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from agent_crm.db import session_scope, with_row_lock
from agent_crm.enums import Brand, EngagementQueryStatus
from agent_crm.hunt.utils import normalize_query, origin_needs_review
from agent_crm.models import EngagementQuery


class EngagementQueryStore:
    """Persist engagement queries. The table only grows."""

    @staticmethod
    def make_dedupe_key(brand: Brand, query: str) -> str:
        return f"{brand.value}|{normalize_query(query)}"

    def enqueue_query(
        self,
        *,
        query: str,
        brand: Brand,
        origin: str = "seed",
        hunt_resource_id: int | None = None,
    ) -> bool:
        """Insert a query if new. Never deletes or replaces an existing row.

        Failed rows with the same dedupe_key are reset to PENDING (retry).
        Completed and pending rows are left untouched so the queue cannot shrink.
        """
        cleaned = query.strip()
        if not cleaned:
            return False
        dedupe_key = self.make_dedupe_key(brand, cleaned)
        initial = (
            EngagementQueryStatus.PENDING_REVIEW
            if origin_needs_review(origin)
            else EngagementQueryStatus.PENDING
        )
        try:
            with session_scope() as session:
                existing = session.scalar(
                    select(EngagementQuery).where(EngagementQuery.dedupe_key == dedupe_key)
                )
                if existing is not None:
                    if existing.status == EngagementQueryStatus.FAILED:
                        existing.status = initial
                        existing.error_message = None
                        existing.completed_at = None
                        existing.origin = origin[:128]
                        if hunt_resource_id is not None:
                            existing.hunt_resource_id = hunt_resource_id
                        return True
                    return False
                session.add(
                    EngagementQuery(
                        query=cleaned,
                        origin=origin[:128],
                        brand=brand,
                        hunt_resource_id=hunt_resource_id,
                        status=initial,
                        dedupe_key=dedupe_key,
                    )
                )
                return True
        except IntegrityError:
            return False

    def claim_next_pending_query(
        self, *, brand: Brand | None = None
    ) -> EngagementQuery | None:
        """Atomically select the next pending query and mark it RUNNING."""
        with session_scope() as session:
            stmt = (
                select(EngagementQuery)
                .where(EngagementQuery.status == EngagementQueryStatus.PENDING)
                .order_by(EngagementQuery.id.asc())
            )
            if brand is not None:
                stmt = stmt.where(EngagementQuery.brand == brand)
            stmt = stmt.limit(1)
            row = session.scalar(with_row_lock(stmt, session))
            if row is None:
                return None
            row.status = EngagementQueryStatus.RUNNING
            session.flush()
            return row

    def mark_query_completed(self, query_id: int) -> None:
        with session_scope() as session:
            row = session.get(EngagementQuery, query_id)
            if row is None:
                return
            row.status = EngagementQueryStatus.COMPLETED
            row.completed_at = datetime.now(UTC)
            row.error_message = None

    def mark_query_failed(self, query_id: int, error: str) -> None:
        with session_scope() as session:
            row = session.get(EngagementQuery, query_id)
            if row is None:
                return
            row.status = EngagementQueryStatus.FAILED
            row.error_message = error[:2000]
            row.completed_at = datetime.now(UTC)

    def claim_next_pending_review_query(
        self,
    ) -> tuple[int, Brand, str, str] | None:
        with session_scope() as session:
            stmt = (
                select(EngagementQuery)
                .where(EngagementQuery.status == EngagementQueryStatus.PENDING_REVIEW)
                .order_by(EngagementQuery.id.asc())
                .limit(1)
            )
            row = session.scalar(with_row_lock(stmt, session))
            if row is None:
                return None
            return (row.id, row.brand, row.query, row.origin)

    def mark_query_kept(self, query_id: int) -> None:
        with session_scope() as session:
            row = session.get(EngagementQuery, query_id)
            if row is None or row.status != EngagementQueryStatus.PENDING_REVIEW:
                return
            row.status = EngagementQueryStatus.PENDING
            row.error_message = None

    def mark_query_rejected(self, query_id: int, reason: str) -> None:
        with session_scope() as session:
            row = session.get(EngagementQuery, query_id)
            if row is None:
                return
            row.status = EngagementQueryStatus.REJECTED
            row.error_message = reason[:2000]
            row.completed_at = datetime.now(UTC)

    def count_pending(self, *, brand: Brand | None = None) -> int:
        return self._count(status=EngagementQueryStatus.PENDING, brand=brand)

    def count_all(self, *, brand: Brand | None = None) -> int:
        return self._count(status=None, brand=brand)

    def _count(
        self, *, status: EngagementQueryStatus | None, brand: Brand | None
    ) -> int:
        with session_scope() as session:
            stmt = select(func.count()).select_from(EngagementQuery)
            if status is not None:
                stmt = stmt.where(EngagementQuery.status == status)
            if brand is not None:
                stmt = stmt.where(EngagementQuery.brand == brand)
            return session.scalar(stmt) or 0

    def queue_status(self) -> dict[str, int]:
        with session_scope() as session:
            rows = session.execute(
                select(EngagementQuery.status, func.count()).group_by(EngagementQuery.status)
            ).all()
        counts = {status.value: 0 for status in EngagementQueryStatus}
        for status, count in rows:
            key = status.value if isinstance(status, EngagementQueryStatus) else str(status)
            counts[key] = int(count)
        return {
            "total": sum(counts.values()),
            "pending": counts.get("pending", 0),
            **counts,
        }

    def reset_stale_running_queries(self, *, stale_minutes: int = 30) -> int:
        """Return stuck RUNNING engagement queries to PENDING (crash recovery)."""
        filters = [EngagementQuery.status == EngagementQueryStatus.RUNNING]
        if stale_minutes > 0:
            cutoff = datetime.now(UTC) - timedelta(minutes=stale_minutes)
            filters.append(EngagementQuery.updated_at < cutoff)
        reset = 0
        with session_scope() as session:
            rows = list(session.scalars(select(EngagementQuery).where(*filters)))
            for row in rows:
                row.status = EngagementQueryStatus.PENDING
                row.error_message = None
                reset += 1
        return reset
