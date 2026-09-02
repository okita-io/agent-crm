"""Persist catalogued engagement venues, threads, and comment drafts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from agent_crm.db import session_scope
from .runner import EngagementSignals
from agent_crm.enums import (
    Brand,
    EngagementDraftStatus,
    EngagementThreadStatus,
    HuntResourceKind,
)
from agent_crm.hunt.utils import canonical_url, is_junk_title
from agent_crm.models import EngagementDraft, EngagementThread, HuntResource

VENUE_KINDS: tuple[HuntResourceKind, ...] = (
    HuntResourceKind.COMMUNITY,
    HuntResourceKind.FORUM,
    HuntResourceKind.SOCIAL,
)


def upsert_thread(
    *,
    url: str,
    brand: Brand,
    title: str | None,
    signals: EngagementSignals,
    hunt_resource_id: int | None = None,
    platform: str | None = None,
    venue_url: str | None = None,
    excerpt: str | None = None,
    found_via_query: str | None = None,
    scanned: bool = False,
) -> EngagementThread | None:
    """Insert or bump a thread. Returns None for junk titles with no prior row."""
    clean_url = canonical_url(url)
    if is_junk_title(title):
        title = None
    now = datetime.now(UTC)
    keywords = list(signals.trend_keywords) or None
    snippet = (excerpt or "").strip()[:800] or None

    with session_scope() as session:
        row = session.scalar(
            select(EngagementThread).where(EngagementThread.url == clean_url)
        )
        if row is None:
            row = EngagementThread(
                url=clean_url,
                hunt_resource_id=hunt_resource_id,
                brand=brand,
                title=title,
                platform=platform,
                venue_url=venue_url,
                popularity_score=signals.score,
                comment_count=signals.comment_count,
                trend_keywords=keywords,
                excerpt=snippet,
                found_via_query=found_via_query,
                status=EngagementThreadStatus.SCANNED
                if scanned
                else EngagementThreadStatus.CATALOGED,
                last_scanned_at=now if scanned else None,
            )
            session.add(row)
        else:
            if title:
                row.title = title
            if hunt_resource_id is not None:
                row.hunt_resource_id = hunt_resource_id
            if platform:
                row.platform = platform
            if venue_url:
                row.venue_url = venue_url
            row.popularity_score = max(row.popularity_score, signals.score)
            if signals.comment_count is not None:
                row.comment_count = max(row.comment_count or 0, signals.comment_count)
            if keywords:
                existing = list(row.trend_keywords or [])
                merged = list(dict.fromkeys(existing + keywords))
                row.trend_keywords = merged[:12]
            if snippet:
                row.excerpt = snippet
            if found_via_query:
                row.found_via_query = found_via_query
            if scanned:
                row.last_scanned_at = now
                if row.status == EngagementThreadStatus.CATALOGED:
                    row.status = EngagementThreadStatus.SCANNED
        session.flush()
        session.refresh(row)
        return row


def list_threads(
    *,
    brand: Brand | None = None,
    status: EngagementThreadStatus | None = None,
    min_score: int = 0,
    limit: int | None = 200,
) -> list[EngagementThread]:
    with session_scope() as session:
        stmt = select(EngagementThread).order_by(
            EngagementThread.popularity_score.desc(),
            EngagementThread.updated_at.desc(),
        )
        if brand is not None:
            stmt = stmt.where(EngagementThread.brand == brand)
        if status is not None:
            stmt = stmt.where(EngagementThread.status == status)
        if min_score > 0:
            stmt = stmt.where(EngagementThread.popularity_score >= min_score)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(session.scalars(stmt))


def list_threads_due_for_scan(
    *,
    brand: Brand | None = None,
    now: datetime | None = None,
    limit: int = 20,
) -> list[EngagementThread]:
    reference = now or datetime.now(UTC)
    with session_scope() as session:
        stmt = (
            select(EngagementThread)
            .where(
                or_(
                    EngagementThread.next_scan_at.is_(None),
                    EngagementThread.next_scan_at <= reference,
                )
            )
            .order_by(
                EngagementThread.popularity_score.desc(),
                EngagementThread.id.asc(),
            )
            .limit(limit)
        )
        if brand is not None:
            stmt = stmt.where(EngagementThread.brand == brand)
        return list(session.scalars(stmt))


def list_venues_due_for_scan(
    *,
    brand: Brand | None = None,
    now: datetime | None = None,
    limit: int = 10,
) -> list[HuntResource]:
    reference = now or datetime.now(UTC)
    with session_scope() as session:
        stmt = (
            select(HuntResource)
            .where(HuntResource.kind.in_(VENUE_KINDS))
            .where(
                or_(
                    HuntResource.next_engagement_scan.is_(None),
                    HuntResource.next_engagement_scan <= reference,
                )
            )
            .order_by(
                HuntResource.engagement_score.desc(),
                HuntResource.last_seen.desc(),
            )
            .limit(limit)
        )
        if brand is not None:
            stmt = stmt.where(HuntResource.brand == brand)
        return list(session.scalars(stmt))


def list_engagement_venues(
    *,
    brand: Brand | None = None,
    limit: int = 200,
) -> list[HuntResource]:
    """All catalogued community/forum/social venues (not only those due for rescan)."""
    with session_scope() as session:
        stmt = (
            select(HuntResource)
            .where(HuntResource.kind.in_(VENUE_KINDS))
            .order_by(
                HuntResource.engagement_score.desc(),
                HuntResource.last_seen.desc(),
            )
            .limit(limit)
        )
        if brand is not None:
            stmt = stmt.where(HuntResource.brand == brand)
        return list(session.scalars(stmt))


def mark_venue_scanned(
    resource_id: int,
    *,
    interval_hours: int,
    now: datetime | None = None,
) -> None:
    reference = now or datetime.now(UTC)
    with session_scope() as session:
        row = session.get(HuntResource, resource_id)
        if row is None:
            return
        row.last_engagement_scan = reference
        row.next_engagement_scan = reference + timedelta(hours=max(interval_hours, 1))


def mark_thread_scanned(
    thread_id: int,
    *,
    interval_hours: int,
    now: datetime | None = None,
) -> None:
    reference = now or datetime.now(UTC)
    with session_scope() as session:
        row = session.get(EngagementThread, thread_id)
        if row is None:
            return
        row.last_scanned_at = reference
        row.next_scan_at = reference + timedelta(hours=max(interval_hours, 1))
        if row.status == EngagementThreadStatus.CATALOGED:
            row.status = EngagementThreadStatus.SCANNED


def mark_thread_draft_ready(thread_id: int) -> None:
    with session_scope() as session:
        row = session.get(EngagementThread, thread_id)
        if row is None:
            return
        row.status = EngagementThreadStatus.DRAFT_READY


def has_draft(thread_id: int, brand: Brand) -> bool:
    with session_scope() as session:
        row = session.scalar(
            select(EngagementDraft).where(
                EngagementDraft.thread_id == thread_id,
                EngagementDraft.brand == brand,
            )
        )
        return row is not None


def upsert_draft(
    *,
    thread_id: int,
    brand: Brand,
    draft_text: str,
    product_angle: str | None = None,
) -> EngagementDraft | None:
    text = draft_text.strip()
    if not text:
        return None
    with session_scope() as session:
        row = session.scalar(
            select(EngagementDraft).where(
                EngagementDraft.thread_id == thread_id,
                EngagementDraft.brand == brand,
            )
        )
        if row is None:
            row = EngagementDraft(
                thread_id=thread_id,
                brand=brand,
                draft_text=text,
                product_angle=(product_angle or "").strip() or None,
                status=EngagementDraftStatus.DRAFT,
            )
            session.add(row)
        else:
            row.draft_text = text
            if product_angle:
                row.product_angle = product_angle.strip()
        session.flush()
        session.refresh(row)
        return row


def list_drafts(
    *,
    brand: Brand | None = None,
    status: EngagementDraftStatus | None = None,
    limit: int | None = 200,
) -> list[EngagementDraft]:
    with session_scope() as session:
        stmt = (
            select(EngagementDraft)
            .options(selectinload(EngagementDraft.thread))
            .order_by(EngagementDraft.updated_at.desc())
        )
        if brand is not None:
            stmt = stmt.where(EngagementDraft.brand == brand)
        if status is not None:
            stmt = stmt.where(EngagementDraft.status == status)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(session.scalars(stmt))


def count_threads(*, brand: Brand | None = None) -> int:
    with session_scope() as session:
        stmt = select(func.count()).select_from(EngagementThread)
        if brand is not None:
            stmt = stmt.where(EngagementThread.brand == brand)
        return int(session.scalar(stmt) or 0)


def count_drafts(*, brand: Brand | None = None) -> int:
    with session_scope() as session:
        stmt = select(func.count()).select_from(EngagementDraft)
        if brand is not None:
            stmt = stmt.where(EngagementDraft.brand == brand)
        return int(session.scalar(stmt) or 0)
