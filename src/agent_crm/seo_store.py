"""Persist SEO targets, reviews, and implementation plans."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .config import get_settings
from .db import session_scope
from .enums import (
    Brand,
    SeoPlanKind,
    SeoPlanStatus,
    SeoReviewKind,
    SeoReviewStatus,
    SeoTargetRole,
)
from .hunt_utils import canonical_url, registrable_domain
from .models import SeoPlan, SeoReview, SeoTarget


def upsert_target(
    *,
    url: str,
    brand: Brand,
    role: SeoTargetRole,
    title: str | None = None,
    notes: str | None = None,
) -> SeoTarget:
    clean = canonical_url(url)
    domain = registrable_domain(clean)
    with session_scope() as session:
        row = session.scalar(
            select(SeoTarget).where(SeoTarget.url == clean, SeoTarget.brand == brand)
        )
        if row is None:
            row = SeoTarget(
                url=clean,
                domain=domain,
                brand=brand,
                role=role,
                title=(title or "").strip() or None,
                notes=(notes or "").strip() or None,
            )
            session.add(row)
        else:
            row.role = role
            if title:
                row.title = title.strip()
            if notes:
                row.notes = notes.strip()
        session.flush()
        session.refresh(row)
        return row


def get_target(target_id: int) -> SeoTarget | None:
    with session_scope() as session:
        return session.get(SeoTarget, target_id)


def list_targets(
    *,
    brand: Brand | None = None,
    role: SeoTargetRole | None = None,
    limit: int | None = 200,
) -> list[SeoTarget]:
    with session_scope() as session:
        stmt = select(SeoTarget).order_by(SeoTarget.updated_at.desc())
        if brand is not None:
            stmt = stmt.where(SeoTarget.brand == brand)
        if role is not None:
            stmt = stmt.where(SeoTarget.role == role)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(session.scalars(stmt))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def review_zone() -> ZoneInfo:
    return ZoneInfo(get_settings().seo_review_timezone)


def next_noon_at(now: datetime | None = None) -> datetime:
    """Return the next local noon (default America/Los_Angeles) as UTC.

    If ``now`` is already at or after today's noon, the slot is tomorrow.
    That keeps reviews to at least once a day, aligned to 12:00 ranch time.
    """
    settings = get_settings()
    tz = review_zone()
    hour = max(0, min(23, settings.seo_review_hour))
    reference = _as_utc(now or datetime.now(UTC)).astimezone(tz)
    today_slot = reference.replace(hour=hour, minute=0, second=0, microsecond=0)
    slot = today_slot if reference < today_slot else today_slot + timedelta(days=1)
    return slot.astimezone(UTC)


def align_review_schedule(*, now: datetime | None = None) -> None:
    """Due now unless last reviewed today, in which case wait until next noon."""
    reference = _as_utc(now or datetime.now(UTC))
    tz = review_zone()
    local_today = reference.astimezone(tz).date()
    nxt = next_noon_at(reference)
    with session_scope() as session:
        for row in session.scalars(select(SeoTarget)):
            if row.last_reviewed_at is None:
                row.next_review_at = None
                continue
            reviewed = _as_utc(row.last_reviewed_at).astimezone(tz).date()
            row.next_review_at = nxt if reviewed == local_today else None


def earliest_next_review_at() -> datetime | None:
    with session_scope() as session:
        value = session.scalar(
            select(func.min(SeoTarget.next_review_at)).where(
                SeoTarget.next_review_at.is_not(None)
            )
        )
        return _as_utc(value) if value is not None else None


def list_targets_due(
    *,
    brand: Brand | None = None,
    now: datetime | None = None,
    limit: int = 200,
) -> list[SeoTarget]:
    reference = now or datetime.now(UTC)
    with session_scope() as session:
        stmt = (
            select(SeoTarget)
            .where(
                (SeoTarget.next_review_at.is_(None))
                | (SeoTarget.next_review_at <= reference)
            )
            .order_by(SeoTarget.id.asc())
            .limit(limit)
        )
        if brand is not None:
            stmt = stmt.where(SeoTarget.brand == brand)
        return list(session.scalars(stmt))


def mark_target_reviewed(
    target_id: int,
    *,
    now: datetime | None = None,
) -> None:
    reference = _as_utc(now or datetime.now(UTC))
    with session_scope() as session:
        row = session.get(SeoTarget, target_id)
        if row is None:
            return
        row.last_reviewed_at = reference
        row.next_review_at = next_noon_at(reference)


def upsert_review(
    *,
    url: str,
    brand: Brand,
    kind: SeoReviewKind,
    title: str,
    body: str,
    target_id: int | None = None,
    score: int | None = None,
    one_thing: str | None = None,
    issues: list | None = None,
    evidence: dict | None = None,
    source_query: str | None = None,
) -> SeoReview | None:
    text = body.strip()
    if not text:
        return None
    clean = canonical_url(url)
    with session_scope() as session:
        stmt = select(SeoReview).where(
            SeoReview.url == clean,
            SeoReview.brand == brand,
            SeoReview.kind == kind,
        )
        if target_id is not None:
            stmt = stmt.where(SeoReview.target_id == target_id)
        row = session.scalar(stmt.order_by(SeoReview.id.desc()))
        if row is None or row.status in {SeoReviewStatus.ACCEPTED, SeoReviewStatus.REJECTED}:
            row = SeoReview(
                target_id=target_id,
                url=clean,
                domain=registrable_domain(clean),
                brand=brand,
                kind=kind,
                title=title.strip()[:512],
                score=score,
                one_thing=(one_thing or "").strip() or None,
                body=text,
                issues=issues,
                evidence=evidence,
                source_query=(source_query or "")[:500] or None,
                status=SeoReviewStatus.DRAFT,
            )
            session.add(row)
        else:
            row.title = title.strip()[:512]
            row.body = text
            row.score = score
            row.one_thing = (one_thing or "").strip() or None
            row.issues = issues
            row.evidence = evidence
            if source_query:
                row.source_query = source_query[:500]
            if target_id is not None:
                row.target_id = target_id
        session.flush()
        session.refresh(row)
        return row


def upsert_plan(
    *,
    url: str,
    brand: Brand,
    kind: SeoPlanKind,
    title: str,
    body: str,
    target_id: int | None = None,
    review_id: int | None = None,
    one_thing: str | None = None,
    tasks: list | None = None,
) -> SeoPlan | None:
    text = body.strip()
    if not text:
        return None
    clean = canonical_url(url)
    with session_scope() as session:
        stmt = select(SeoPlan).where(
            SeoPlan.url == clean,
            SeoPlan.brand == brand,
            SeoPlan.kind == kind,
        )
        if target_id is not None:
            stmt = stmt.where(SeoPlan.target_id == target_id)
        row = session.scalar(stmt.order_by(SeoPlan.id.desc()))
        if row is None or row.status in {SeoPlanStatus.APPROVED, SeoPlanStatus.REJECTED}:
            row = SeoPlan(
                target_id=target_id,
                review_id=review_id,
                url=clean,
                domain=registrable_domain(clean),
                brand=brand,
                kind=kind,
                title=title.strip()[:512],
                one_thing=(one_thing or "").strip() or None,
                body=text,
                tasks=tasks,
                status=SeoPlanStatus.DRAFT,
            )
            session.add(row)
        else:
            row.title = title.strip()[:512]
            row.body = text
            row.one_thing = (one_thing or "").strip() or None
            row.tasks = tasks
            if review_id is not None:
                row.review_id = review_id
            if target_id is not None:
                row.target_id = target_id
        session.flush()
        session.refresh(row)
        return row


def list_reviews(
    *,
    brand: Brand | None = None,
    kind: SeoReviewKind | None = None,
    status: SeoReviewStatus | None = None,
    limit: int | None = 200,
) -> list[SeoReview]:
    with session_scope() as session:
        stmt = select(SeoReview).order_by(SeoReview.updated_at.desc())
        if brand is not None:
            stmt = stmt.where(SeoReview.brand == brand)
        if kind is not None:
            stmt = stmt.where(SeoReview.kind == kind)
        if status is not None:
            stmt = stmt.where(SeoReview.status == status)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(session.scalars(stmt))


def list_plans(
    *,
    brand: Brand | None = None,
    kind: SeoPlanKind | None = None,
    status: SeoPlanStatus | None = None,
    limit: int | None = 200,
) -> list[SeoPlan]:
    with session_scope() as session:
        stmt = (
            select(SeoPlan)
            .options(selectinload(SeoPlan.review))
            .order_by(SeoPlan.updated_at.desc())
        )
        if brand is not None:
            stmt = stmt.where(SeoPlan.brand == brand)
        if kind is not None:
            stmt = stmt.where(SeoPlan.kind == kind)
        if status is not None:
            stmt = stmt.where(SeoPlan.status == status)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(session.scalars(stmt))


def count_reviews(*, brand: Brand | None = None) -> int:
    with session_scope() as session:
        stmt = select(func.count()).select_from(SeoReview)
        if brand is not None:
            stmt = stmt.where(SeoReview.brand == brand)
        return int(session.scalar(stmt) or 0)


def count_plans(*, brand: Brand | None = None) -> int:
    with session_scope() as session:
        stmt = select(func.count()).select_from(SeoPlan)
        if brand is not None:
            stmt = stmt.where(SeoPlan.brand == brand)
        return int(session.scalar(stmt) or 0)


def count_targets(*, brand: Brand | None = None) -> int:
    with session_scope() as session:
        stmt = select(func.count()).select_from(SeoTarget)
        if brand is not None:
            stmt = stmt.where(SeoTarget.brand == brand)
        return int(session.scalar(stmt) or 0)
