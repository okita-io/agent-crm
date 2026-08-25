"""Database operations for the hunter queue, resources, and heartbeats."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from agent_crm.db import session_scope
from agent_crm.enums import AgentHeartbeatStatus, Brand, HuntQueryStatus, HuntResourceKind
from agent_crm.hunt_utils import (
    canonical_url,
    classify_resource,
    is_junk_title,
    is_junk_url,
    make_dedupe_key,
    normalize_query,
    registrable_domain,
)
from agent_crm.models import AgentHeartbeat, HuntQuery, HuntResource


class HuntStore:
    """Persist queue state, discovered resources, and agent heartbeats."""

    ACTOR = "outbound_hunter"

    def set_heartbeat(self, status: AgentHeartbeatStatus, message: str | None = None) -> None:
        with session_scope() as session:
            row = session.scalar(
                select(AgentHeartbeat).where(AgentHeartbeat.actor == self.ACTOR)
            )
            if row is None:
                row = AgentHeartbeat(actor=self.ACTOR, status=status, message=message)
                session.add(row)
            else:
                row.status = status
                row.message = message
                row.updated_at = datetime.now(UTC)

    def enqueue_query(
        self,
        *,
        query: str,
        brand: Brand,
        params: dict | None = None,
        origin: str = "seed",
        run_id: str | None = None,
    ) -> bool:
        """Enqueue a query if not already present. Returns True if enqueued."""
        dedupe_key = make_dedupe_key(query, params)
        with session_scope() as session:
            existing = session.scalar(
                select(HuntQuery).where(HuntQuery.dedupe_key == dedupe_key)
            )
            if existing is not None:
                return False
            session.add(
                HuntQuery(
                    query=query.strip(),
                    params=params,
                    origin=origin,
                    brand=brand,
                    status=HuntQueryStatus.PENDING,
                    dedupe_key=dedupe_key,
                    run_id=run_id,
                )
            )
            return True

    def next_pending_query(self, run_id: str | None = None, brand: Brand | None = None) -> HuntQuery | None:
        with session_scope() as session:
            stmt = (
                select(HuntQuery)
                .where(HuntQuery.status == HuntQueryStatus.PENDING)
                .order_by(HuntQuery.id.asc())
            )
            if run_id is not None:
                stmt = stmt.where(HuntQuery.run_id == run_id)
            if brand is not None:
                stmt = stmt.where(HuntQuery.brand == brand)
            return session.scalar(stmt)

    def count_pending(self, brand: Brand | None = None) -> int:
        with session_scope() as session:
            stmt = (
                select(func.count())
                .select_from(HuntQuery)
                .where(HuntQuery.status == HuntQueryStatus.PENDING)
            )
            if brand is not None:
                stmt = stmt.where(HuntQuery.brand == brand)
            return session.scalar(stmt) or 0

    def mark_query_running(self, query_id: int) -> None:
        with session_scope() as session:
            row = session.get(HuntQuery, query_id)
            if row is None:
                return
            row.status = HuntQueryStatus.RUNNING

    def mark_query_completed(self, query_id: int) -> None:
        with session_scope() as session:
            row = session.get(HuntQuery, query_id)
            if row is None:
                return
            row.status = HuntQueryStatus.COMPLETED
            row.completed_at = datetime.now(UTC)

    def mark_query_failed(self, query_id: int, error: str) -> None:
        with session_scope() as session:
            row = session.get(HuntQuery, query_id)
            if row is None:
                return
            row.status = HuntQueryStatus.FAILED
            row.error_message = error[:2000]
            row.completed_at = datetime.now(UTC)

    def upsert_resource(
        self,
        *,
        url: str,
        brand: Brand,
        title: str | None,
        found_via_query: str,
        snippet: str | None = None,
        kind: HuntResourceKind | None = None,
    ) -> HuntResource | None:
        """Insert or bump a discovered resource. Returns None for junk URLs."""
        if is_junk_url(url):
            return None
        clean_url = canonical_url(url)
        if is_junk_url(clean_url):
            return None
        if is_junk_title(title):
            title = None

        domain = registrable_domain(clean_url)
        resource_kind = kind or classify_resource(clean_url, title, snippet)
        now = datetime.now(UTC)

        with session_scope() as session:
            row = session.scalar(select(HuntResource).where(HuntResource.url == clean_url))
            if row is None:
                row = HuntResource(
                    url=clean_url,
                    domain=domain,
                    title=title,
                    brand=brand,
                    kind=resource_kind,
                    found_via_query=found_via_query,
                    first_seen=now,
                    last_seen=now,
                    hit_count=1,
                    notes=snippet,
                )
                session.add(row)
            else:
                row.last_seen = now
                row.hit_count += 1
                if title and not is_junk_title(title):
                    row.title = title
                if snippet:
                    row.notes = snippet
                row.found_via_query = found_via_query
            session.flush()
            session.refresh(row)
            return row

    def list_resources(
        self,
        *,
        brand: Brand | None = None,
        limit: int = 500,
    ) -> list[HuntResource]:
        with session_scope() as session:
            stmt = select(HuntResource).order_by(HuntResource.last_seen.desc()).limit(limit)
            if brand is not None:
                stmt = stmt.where(HuntResource.brand == brand)
            return list(session.scalars(stmt))

    def queue_status(self, run_id: str | None = None) -> dict:
        with session_scope() as session:
            stmt = select(HuntQuery.status, func.count()).group_by(HuntQuery.status)
            if run_id is not None:
                stmt = stmt.where(HuntQuery.run_id == run_id)
            counts = {status.value: count for status, count in session.execute(stmt)}
            pending = session.scalar(
                select(func.count())
                .select_from(HuntQuery)
                .where(
                    HuntQuery.status == HuntQueryStatus.PENDING,
                    *([HuntQuery.run_id == run_id] if run_id else []),
                )
            )
            return {
                "pending": pending or 0,
                "by_status": counts,
                "total_resources": session.scalar(select(func.count()).select_from(HuntResource))
                or 0,
            }

    def has_completed_query(self, query: str, params: dict | None) -> bool:
        dedupe_key = make_dedupe_key(query, params)
        with session_scope() as session:
            row = session.scalar(
                select(HuntQuery).where(
                    HuntQuery.dedupe_key == dedupe_key,
                    HuntQuery.status == HuntQueryStatus.COMPLETED,
                )
            )
            return row is not None

    @staticmethod
    def normalize_term(term: str) -> str:
        return normalize_query(term)
