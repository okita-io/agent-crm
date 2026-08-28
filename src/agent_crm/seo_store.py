"""Persist SEO targets, reviews, and implementation plans."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

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
    limit: int = 200,
) -> list[SeoTarget]:
    with session_scope() as session:
        stmt = select(SeoTarget).order_by(SeoTarget.updated_at.desc()).limit(limit)
        if brand is not None:
            stmt = stmt.where(SeoTarget.brand == brand)
        if role is not None:
            stmt = stmt.where(SeoTarget.role == role)
        return list(session.scalars(stmt))


def list_targets_due(
    *,
    brand: Brand | None = None,
    now: datetime | None = None,
    limit: int = 50,
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
    interval_hours: int,
    now: datetime | None = None,
) -> None:
    reference = now or datetime.now(UTC)
    with session_scope() as session:
        row = session.get(SeoTarget, target_id)
        if row is None:
            return
        row.last_reviewed_at = reference
        row.next_review_at = reference + timedelta(hours=max(interval_hours, 1))


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
    limit: int = 200,
) -> list[SeoReview]:
    with session_scope() as session:
        stmt = select(SeoReview).order_by(SeoReview.updated_at.desc()).limit(limit)
        if brand is not None:
            stmt = stmt.where(SeoReview.brand == brand)
        if kind is not None:
            stmt = stmt.where(SeoReview.kind == kind)
        if status is not None:
            stmt = stmt.where(SeoReview.status == status)
        return list(session.scalars(stmt))


def list_plans(
    *,
    brand: Brand | None = None,
    kind: SeoPlanKind | None = None,
    status: SeoPlanStatus | None = None,
    limit: int = 200,
) -> list[SeoPlan]:
    with session_scope() as session:
        stmt = (
            select(SeoPlan)
            .options(selectinload(SeoPlan.review))
            .order_by(SeoPlan.updated_at.desc())
            .limit(limit)
        )
        if brand is not None:
            stmt = stmt.where(SeoPlan.brand == brand)
        if kind is not None:
            stmt = stmt.where(SeoPlan.kind == kind)
        if status is not None:
            stmt = stmt.where(SeoPlan.status == status)
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
