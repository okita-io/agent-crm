"""Database operations for the hunter query queue and resource collection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from agent_crm.db import session_scope, with_row_lock
from agent_crm.enums import AgentStatus, Brand, HuntQueryStatus, HuntResourceKind
from agent_crm.hunt_priority import hunt_query_priority
from agent_crm.hunt_seeds import audience_from_origin
from agent_crm.heartbeat import record_heartbeat
from agent_crm.hunt_utils import (
    ResourceClassification,
    canonical_url,
    classify_resource_detailed,
    format_resource_notes,
    is_junk_title,
    is_junk_url,
    make_dedupe_key,
    normalize_query,
    registrable_domain,
)
from agent_crm.models import HuntQuery, HuntResource


@dataclass
class UpsertResourceResult:
    resource: HuntResource | None
    is_new: bool
    classification: ResourceClassification | None = None


class HuntStore:
    """Persist queue state and discovered resources."""

    ACTOR = "outbound_hunter"

    def set_heartbeat(
        self,
        status: AgentStatus,
        task: str | None = None,
        *,
        resource: str | None = None,
    ) -> None:
        record_heartbeat(self.ACTOR, status=status, task=task, resource=resource)

    def enqueue_query(
        self,
        *,
        query: str,
        brand: Brand,
        params: dict | None = None,
        origin: str = "seed",
        run_id: str | None = None,
        priority: int | None = None,
    ) -> bool:
        """Enqueue a query if not already present. Returns True if enqueued.

        Failed rows with the same dedupe_key are reset to PENDING (retry).
        """
        dedupe_key = make_dedupe_key(query, params)
        if priority is None:
            priority = hunt_query_priority(brand, audience_from_origin(origin))
        try:
            with session_scope() as session:
                existing = session.scalar(
                    select(HuntQuery).where(HuntQuery.dedupe_key == dedupe_key)
                )
                if existing is not None:
                    if existing.status == HuntQueryStatus.FAILED:
                        existing.status = HuntQueryStatus.PENDING
                        existing.error_message = None
                        existing.completed_at = None
                        existing.priority = priority
                        existing.origin = origin
                        if run_id is not None:
                            existing.run_id = run_id
                        return True
                    return False
                session.add(
                    HuntQuery(
                        query=query.strip(),
                        params=params,
                        origin=origin,
                        brand=brand,
                        priority=priority,
                        status=HuntQueryStatus.PENDING,
                        dedupe_key=dedupe_key,
                        run_id=run_id,
                    )
                )
                return True
        except IntegrityError:
            return False

    def next_pending_query(
        self, run_id: str | None = None, brand: Brand | None = None
    ) -> HuntQuery | None:
        """Return the next pending query without claiming it (read-only)."""
        with session_scope() as session:
            stmt = (
                select(HuntQuery)
                .where(HuntQuery.status == HuntQueryStatus.PENDING)
                .order_by(HuntQuery.priority.desc(), HuntQuery.id.asc())
            )
            if run_id is not None:
                stmt = stmt.where(HuntQuery.run_id == run_id)
            if brand is not None:
                stmt = stmt.where(HuntQuery.brand == brand)
            return session.scalar(stmt)

    def claim_next_pending_query(
        self, run_id: str | None = None, brand: Brand | None = None
    ) -> HuntQuery | None:
        """Atomically select the next pending query and mark it RUNNING."""
        with session_scope() as session:
            stmt = (
                select(HuntQuery)
                .where(HuntQuery.status == HuntQueryStatus.PENDING)
                .order_by(HuntQuery.priority.desc(), HuntQuery.id.asc())
            )
            if run_id is not None:
                stmt = stmt.where(HuntQuery.run_id == run_id)
            if brand is not None:
                stmt = stmt.where(HuntQuery.brand == brand)
            stmt = stmt.limit(1)
            row = session.scalar(with_row_lock(stmt, session))
            if row is None:
                return None
            row.status = HuntQueryStatus.RUNNING
            session.flush()
            return row

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

    def reset_stale_running_queries(self, *, stale_minutes: int = 30) -> int:
        """Return stuck RUNNING hunt queries to PENDING (crash recovery)."""
        cutoff = datetime.now(UTC) - timedelta(minutes=stale_minutes)
        reset = 0
        with session_scope() as session:
            rows = list(
                session.scalars(
                    select(HuntQuery).where(
                        HuntQuery.status == HuntQueryStatus.RUNNING,
                        HuntQuery.updated_at < cutoff,
                    )
                )
            )
            for row in rows:
                row.status = HuntQueryStatus.PENDING
                row.error_message = None
                reset += 1
        return reset

    def upsert_resource(
        self,
        *,
        url: str,
        brand: Brand,
        title: str | None,
        found_via_query: str,
        snippet: str | None = None,
        kind: HuntResourceKind | None = None,
    ) -> UpsertResourceResult:
        """Insert or bump a discovered resource. Returns None resource for junk URLs."""
        if is_junk_url(url):
            return UpsertResourceResult(resource=None, is_new=False)
        clean_url = canonical_url(url)
        if is_junk_url(clean_url):
            return UpsertResourceResult(resource=None, is_new=False)
        if is_junk_title(title):
            title = None

        domain = registrable_domain(clean_url)
        classification = classify_resource_detailed(clean_url, title, snippet)
        resource_kind = kind or classification.kind
        classification = ResourceClassification(
            kind=resource_kind,
            community_slug=classification.community_slug,
            community_label=classification.community_label or title,
            platform=classification.platform,
        )
        notes = format_resource_notes(classification, snippet)
        now = datetime.now(UTC)

        with session_scope() as session:
            row = session.scalar(select(HuntResource).where(HuntResource.url == clean_url))
            is_new = row is None
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
                    notes=notes,
                )
                session.add(row)
            else:
                row.last_seen = now
                row.hit_count += 1
                if title and not is_junk_title(title):
                    row.title = title
                if notes:
                    row.notes = notes
                if resource_kind != HuntResourceKind.OTHER:
                    row.kind = resource_kind
                row.found_via_query = found_via_query
            session.flush()
            session.refresh(row)
            return UpsertResourceResult(
                resource=row,
                is_new=is_new,
                classification=classification,
            )

    def list_resources(
        self,
        *,
        brand: Brand | None = None,
        limit: int = 500,
        kinds: tuple[HuntResourceKind, ...] | None = None,
    ) -> list[HuntResource]:
        with session_scope() as session:
            stmt = select(HuntResource).order_by(HuntResource.last_seen.desc()).limit(limit)
            if brand is not None:
                stmt = stmt.where(HuntResource.brand == brand)
            if kinds:
                stmt = stmt.where(HuntResource.kind.in_(kinds))
            return list(session.scalars(stmt))

    def list_queries(
        self,
        *,
        brand: Brand | None = None,
        origin_prefix: str | None = None,
        status: HuntQueryStatus | None = None,
        limit: int = 200,
    ) -> list[HuntQuery]:
        with session_scope() as session:
            stmt = select(HuntQuery).order_by(HuntQuery.id.desc()).limit(limit)
            if brand is not None:
                stmt = stmt.where(HuntQuery.brand == brand)
            if origin_prefix is not None:
                stmt = stmt.where(HuntQuery.origin.startswith(origin_prefix))
            if status is not None:
                stmt = stmt.where(HuntQuery.status == status)
            return list(session.scalars(stmt))

    def list_feedback_queries(
        self,
        *,
        brand: Brand | None = None,
        limit: int = 200,
    ) -> list[HuntQuery]:
        """Queries enqueued from community, person, or handle feedback loops."""
        with session_scope() as session:
            stmt = (
                select(HuntQuery)
                .where(
                    HuntQuery.origin.startswith("community:")
                    | HuntQuery.origin.startswith("person:")
                    | HuntQuery.origin.startswith("handle:")
                    | HuntQuery.origin.contains(":community:")
                    | HuntQuery.origin.contains(":person:")
                    | HuntQuery.origin.contains(":handle:")
                )
                .order_by(HuntQuery.id.desc())
                .limit(limit)
            )
            if brand is not None:
                stmt = stmt.where(HuntQuery.brand == brand)
            return list(session.scalars(stmt))

    def current_running_query(
        self,
        *,
        stale_minutes: int = 15,
        now: datetime | None = None,
    ) -> HuntQuery | None:
        """Most recently updated fresh ``running`` query, or ``None`` if stale/absent."""
        reference = now or datetime.now(UTC)
        cutoff = reference - timedelta(minutes=stale_minutes)
        with session_scope() as session:
            stmt = (
                select(HuntQuery)
                .where(
                    HuntQuery.status == HuntQueryStatus.RUNNING,
                    HuntQuery.updated_at >= cutoff,
                )
                .order_by(HuntQuery.updated_at.desc())
                .limit(1)
            )
            return session.scalar(stmt)

    def queue_breakdown(self) -> list[dict]:
        """Counts grouped by brand, priority, and status."""
        with session_scope() as session:
            stmt = (
                select(
                    HuntQuery.brand,
                    HuntQuery.priority,
                    HuntQuery.status,
                    func.count(),
                )
                .group_by(HuntQuery.brand, HuntQuery.priority, HuntQuery.status)
                .order_by(
                    HuntQuery.brand.asc(),
                    HuntQuery.priority.desc(),
                    HuntQuery.status.asc(),
                )
            )
            return [
                {
                    "brand": brand.value,
                    "priority": priority,
                    "status": status.value,
                    "count": count,
                }
                for brand, priority, status, count in session.execute(stmt)
            ]

    def recently_completed_queries(self, *, limit: int = 8) -> list[HuntQuery]:
        with session_scope() as session:
            stmt = (
                select(HuntQuery)
                .where(HuntQuery.status == HuntQueryStatus.COMPLETED)
                .order_by(HuntQuery.completed_at.desc(), HuntQuery.updated_at.desc())
                .limit(limit)
            )
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
