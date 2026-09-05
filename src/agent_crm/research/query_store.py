"""Append-only persistence for the research search-term queue.

Rows are never deleted. Completing a query changes status only; discovered
follow-up terms are inserted if they are new.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from agent_crm.agent_control import activate_queue_review
from agent_crm.db import session_scope, with_row_lock
from agent_crm.enums import Brand, ResearchFindingKind, ResearchQueryStatus
from agent_crm.hunt.utils import normalize_query, origin_needs_review
from agent_crm.models import ResearchQuery


class ResearchQueryStore:
    """Persist research queries. The table only grows."""

    @staticmethod
    def make_dedupe_key(brand: Brand, kind: ResearchFindingKind, query: str) -> str:
        return f"{brand.value}|{kind.value}|{normalize_query(query)}"

    def enqueue_query(
        self,
        *,
        query: str,
        brand: Brand,
        kind: ResearchFindingKind,
        origin: str = "seed",
    ) -> bool:
        """Insert a query if new. Never deletes or replaces an existing row.

        Failed rows with the same dedupe_key are reset to PENDING (retry).
        Completed and pending rows are left untouched so the queue cannot shrink.
        """
        cleaned = query.strip()
        if not cleaned:
            return False
        dedupe_key = self.make_dedupe_key(brand, kind, cleaned)
        initial = (
            ResearchQueryStatus.PENDING_REVIEW
            if origin_needs_review(origin)
            else ResearchQueryStatus.PENDING
        )
        try:
            with session_scope() as session:
                existing = session.scalar(
                    select(ResearchQuery).where(ResearchQuery.dedupe_key == dedupe_key)
                )
                if existing is not None:
                    if existing.status == ResearchQueryStatus.FAILED:
                        existing.status = initial
                        existing.error_message = None
                        existing.completed_at = None
                        existing.origin = origin
                        added = True
                    else:
                        added = False
                else:
                    session.add(
                        ResearchQuery(
                            query=cleaned,
                            origin=origin[:128],
                            brand=brand,
                            kind=kind,
                            status=initial,
                            dedupe_key=dedupe_key,
                        )
                    )
                    added = True
            if added:
                activate_queue_review()
            return added
        except IntegrityError:
            return False

    def claim_next_pending_query(
        self,
        *,
        brand: Brand | None = None,
        kind: ResearchFindingKind | None = None,
    ) -> ResearchQuery | None:
        """Atomically select the next pending query and mark it RUNNING."""
        with session_scope() as session:
            stmt = (
                select(ResearchQuery)
                .where(ResearchQuery.status == ResearchQueryStatus.PENDING)
                .order_by(ResearchQuery.id.asc())
            )
            if brand is not None:
                stmt = stmt.where(ResearchQuery.brand == brand)
            if kind is not None:
                stmt = stmt.where(ResearchQuery.kind == kind)
            stmt = stmt.limit(1)
            row = session.scalar(with_row_lock(stmt, session))
            if row is None:
                return None
            row.status = ResearchQueryStatus.RUNNING
            session.flush()
            return row

    def mark_query_running(self, query_id: int) -> None:
        with session_scope() as session:
            row = session.get(ResearchQuery, query_id)
            if row is None:
                return
            if row.status == ResearchQueryStatus.PENDING:
                row.status = ResearchQueryStatus.RUNNING

    def mark_query_completed(self, query_id: int) -> None:
        with session_scope() as session:
            row = session.get(ResearchQuery, query_id)
            if row is None:
                return
            row.status = ResearchQueryStatus.COMPLETED
            row.completed_at = datetime.now(UTC)
            row.error_message = None

    def mark_query_failed(self, query_id: int, error: str) -> None:
        with session_scope() as session:
            row = session.get(ResearchQuery, query_id)
            if row is None:
                return
            row.status = ResearchQueryStatus.FAILED
            row.error_message = error[:2000]
            row.completed_at = datetime.now(UTC)

    def claim_next_pending_review_query(
        self,
    ) -> tuple[int, Brand, str, str] | None:
        with session_scope() as session:
            stmt = (
                select(ResearchQuery)
                .where(ResearchQuery.status == ResearchQueryStatus.PENDING_REVIEW)
                .order_by(ResearchQuery.id.asc())
                .limit(1)
            )
            row = session.scalar(with_row_lock(stmt, session))
            if row is None:
                return None
            return (row.id, row.brand, row.query, row.origin)

    def mark_query_kept(self, query_id: int) -> None:
        with session_scope() as session:
            row = session.get(ResearchQuery, query_id)
            if row is None or row.status != ResearchQueryStatus.PENDING_REVIEW:
                return
            row.status = ResearchQueryStatus.PENDING
            row.error_message = None

    def mark_query_rejected(self, query_id: int, reason: str) -> None:
        with session_scope() as session:
            row = session.get(ResearchQuery, query_id)
            if row is None:
                return
            row.status = ResearchQueryStatus.REJECTED
            row.error_message = reason[:2000]
            row.completed_at = datetime.now(UTC)

    def get_by_dedupe(
        self, brand: Brand, kind: ResearchFindingKind, query: str
    ) -> ResearchQuery | None:
        dedupe_key = self.make_dedupe_key(brand, kind, query)
        with session_scope() as session:
            return session.scalar(
                select(ResearchQuery).where(ResearchQuery.dedupe_key == dedupe_key)
            )

    def count_pending(
        self,
        *,
        brand: Brand | None = None,
        kind: ResearchFindingKind | None = None,
    ) -> int:
        return self._count(status=ResearchQueryStatus.PENDING, brand=brand, kind=kind)

    def count_all(
        self,
        *,
        brand: Brand | None = None,
        kind: ResearchFindingKind | None = None,
    ) -> int:
        return self._count(status=None, brand=brand, kind=kind)

    def _count(
        self,
        *,
        status: ResearchQueryStatus | None,
        brand: Brand | None,
        kind: ResearchFindingKind | None,
    ) -> int:
        with session_scope() as session:
            stmt = select(func.count()).select_from(ResearchQuery)
            if status is not None:
                stmt = stmt.where(ResearchQuery.status == status)
            if brand is not None:
                stmt = stmt.where(ResearchQuery.brand == brand)
            if kind is not None:
                stmt = stmt.where(ResearchQuery.kind == kind)
            return session.scalar(stmt) or 0

    def queue_status(self) -> dict[str, int]:
        with session_scope() as session:
            rows = session.execute(
                select(ResearchQuery.status, func.count())
                .group_by(ResearchQuery.status)
            ).all()
        counts = {status.value: 0 for status in ResearchQueryStatus}
        for status, count in rows:
            key = status.value if isinstance(status, ResearchQueryStatus) else str(status)
            counts[key] = int(count)
        return {
            "total": sum(counts.values()),
            "pending": counts.get("pending", 0),
            **counts,
        }

    def reset_stale_running_queries(self, *, stale_minutes: int = 30) -> int:
        """Return stuck RUNNING research queries to PENDING (crash recovery)."""
        filters = [ResearchQuery.status == ResearchQueryStatus.RUNNING]
        if stale_minutes > 0:
            cutoff = datetime.now(UTC) - timedelta(minutes=stale_minutes)
            filters.append(ResearchQuery.updated_at < cutoff)
        reset = 0
        with session_scope() as session:
            rows = list(session.scalars(select(ResearchQuery).where(*filters)))
            for row in rows:
                row.status = ResearchQueryStatus.PENDING
                row.error_message = None
                reset += 1
        return reset
