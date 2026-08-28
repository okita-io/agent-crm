"""Append-only persistence for the SEO document job queue.

Rows are never deleted. Completing a query changes status only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .db import session_scope, with_row_lock
from .enums import Brand, SeoQueryKind, SeoQueryStatus
from .hunt_utils import normalize_query
from .models import SeoQuery


class SeoQueryStore:
    """Persist SEO document jobs. The table only grows."""

    @staticmethod
    def make_dedupe_key(brand: Brand, kind: SeoQueryKind, query: str) -> str:
        return f"{brand.value}|{kind.value}|{normalize_query(query)}"

    def enqueue_query(
        self,
        *,
        query: str,
        brand: Brand,
        kind: SeoQueryKind,
        origin: str = "seed",
        target_id: int | None = None,
        reopen_completed: bool = False,
    ) -> bool:
        cleaned = query.strip()
        if not cleaned:
            return False
        dedupe_key = self.make_dedupe_key(brand, kind, cleaned)
        try:
            with session_scope() as session:
                existing = session.scalar(
                    select(SeoQuery).where(SeoQuery.dedupe_key == dedupe_key)
                )
                if existing is not None:
                    reopen = existing.status == SeoQueryStatus.FAILED or (
                        reopen_completed and existing.status == SeoQueryStatus.COMPLETED
                    )
                    if reopen:
                        existing.status = SeoQueryStatus.PENDING
                        existing.error_message = None
                        existing.completed_at = None
                        existing.origin = origin[:128]
                        if target_id is not None:
                            existing.target_id = target_id
                        return True
                    return False
                session.add(
                    SeoQuery(
                        query=cleaned,
                        origin=origin[:128],
                        brand=brand,
                        kind=kind,
                        target_id=target_id,
                        status=SeoQueryStatus.PENDING,
                        dedupe_key=dedupe_key,
                    )
                )
                return True
        except IntegrityError:
            return False

    def claim_next_pending_query(
        self, *, brand: Brand | None = None
    ) -> SeoQuery | None:
        with session_scope() as session:
            stmt = (
                select(SeoQuery)
                .where(SeoQuery.status == SeoQueryStatus.PENDING)
                .order_by(SeoQuery.id.asc())
            )
            if brand is not None:
                stmt = stmt.where(SeoQuery.brand == brand)
            stmt = stmt.limit(1)
            row = session.scalar(with_row_lock(stmt, session))
            if row is None:
                return None
            row.status = SeoQueryStatus.RUNNING
            session.flush()
            return row

    def mark_query_completed(self, query_id: int) -> None:
        with session_scope() as session:
            row = session.get(SeoQuery, query_id)
            if row is None:
                return
            row.status = SeoQueryStatus.COMPLETED
            row.completed_at = datetime.now(UTC)
            row.error_message = None

    def mark_query_failed(self, query_id: int, error: str) -> None:
        with session_scope() as session:
            row = session.get(SeoQuery, query_id)
            if row is None:
                return
            row.status = SeoQueryStatus.FAILED
            row.error_message = error[:2000]
            row.completed_at = datetime.now(UTC)

    def count_pending(self, *, brand: Brand | None = None) -> int:
        return self._count(status=SeoQueryStatus.PENDING, brand=brand)

    def count_all(self, *, brand: Brand | None = None) -> int:
        return self._count(status=None, brand=brand)

    def _count(self, *, status: SeoQueryStatus | None, brand: Brand | None) -> int:
        with session_scope() as session:
            stmt = select(func.count()).select_from(SeoQuery)
            if status is not None:
                stmt = stmt.where(SeoQuery.status == status)
            if brand is not None:
                stmt = stmt.where(SeoQuery.brand == brand)
            return session.scalar(stmt) or 0

    def queue_status(self) -> dict[str, int]:
        with session_scope() as session:
            rows = session.execute(
                select(SeoQuery.status, func.count()).group_by(SeoQuery.status)
            ).all()
        counts = {status.value: 0 for status in SeoQueryStatus}
        for status, count in rows:
            key = status.value if isinstance(status, SeoQueryStatus) else str(status)
            counts[key] = int(count)
        return {
            "total": sum(counts.values()),
            "pending": counts.get("pending", 0),
            **counts,
        }

    def reset_stale_running_queries(self, *, stale_minutes: int = 30) -> int:
        filters = [SeoQuery.status == SeoQueryStatus.RUNNING]
        if stale_minutes > 0:
            cutoff = datetime.now(UTC) - timedelta(minutes=stale_minutes)
            filters.append(SeoQuery.updated_at < cutoff)
        reset = 0
        with session_scope() as session:
            rows = list(session.scalars(select(SeoQuery).where(*filters)))
            for row in rows:
                row.status = SeoQueryStatus.PENDING
                row.error_message = None
                reset += 1
        return reset
