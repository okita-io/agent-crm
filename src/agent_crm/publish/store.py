"""Persist social accounts and publish jobs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from agent_crm.db import session_scope, with_row_lock
from agent_crm.enums import (
    Brand,
    EngagementDraftStatus,
    PublishJobStatus,
    PublishSourceKind,
    SocialPlatform,
)
from agent_crm.models import EngagementDraft, PublishJob, SocialAccount


def create_social_account(
    *,
    brand: Brand,
    platform: SocialPlatform,
    handle: str,
    postiz_integration_id: str | None = None,
    credential_key: str | None = None,
    enabled: bool = True,
    daily_cap: int = 3,
    min_interval_minutes: int = 240,
) -> SocialAccount:
    clean_handle = handle.strip().lstrip("@")
    if not clean_handle:
        raise ValueError("handle is required")
    with session_scope() as session:
        row = SocialAccount(
            brand=brand,
            platform=platform,
            handle=clean_handle,
            postiz_integration_id=(postiz_integration_id or "").strip() or None,
            credential_key=(credential_key or "").strip() or None,
            enabled=enabled,
            daily_cap=max(daily_cap, 1),
            min_interval_minutes=max(min_interval_minutes, 1),
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return row


def list_social_accounts(
    *,
    brand: Brand | None = None,
    platform: SocialPlatform | None = None,
    enabled_only: bool = False,
    limit: int = 200,
) -> list[SocialAccount]:
    with session_scope() as session:
        stmt = select(SocialAccount).order_by(
            SocialAccount.brand.asc(),
            SocialAccount.platform.asc(),
            SocialAccount.handle.asc(),
        )
        if brand is not None:
            stmt = stmt.where(SocialAccount.brand == brand)
        if platform is not None:
            stmt = stmt.where(SocialAccount.platform == platform)
        if enabled_only:
            stmt = stmt.where(SocialAccount.enabled.is_(True))
        stmt = stmt.limit(limit)
        return list(session.scalars(stmt))


def get_social_account(account_id: int) -> SocialAccount | None:
    with session_scope() as session:
        return session.get(SocialAccount, account_id)


def get_engagement_draft(draft_id: int) -> EngagementDraft | None:
    with session_scope() as session:
        return session.scalar(
            select(EngagementDraft)
            .options(selectinload(EngagementDraft.thread))
            .where(EngagementDraft.id == draft_id)
        )


def count_posts_since(
    account_id: int,
    *,
    since: datetime,
) -> int:
    with session_scope() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(PublishJob)
                .where(
                    PublishJob.account_id == account_id,
                    PublishJob.status == PublishJobStatus.POSTED,
                    PublishJob.updated_at >= since,
                )
            )
            or 0
        )


def next_slot_for_account(
    account: SocialAccount,
    *,
    now: datetime | None = None,
) -> datetime:
    """Earliest time this account may send, given daily_cap and min_interval."""
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    candidates = [reference]
    if account.last_posted_at is not None:
        last = account.last_posted_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        candidates.append(last + timedelta(minutes=account.min_interval_minutes))
    day_start = reference.replace(hour=0, minute=0, second=0, microsecond=0)
    posted_today = count_posts_since(account.id, since=day_start)
    if posted_today >= account.daily_cap:
        tomorrow = day_start + timedelta(days=1)
        candidates.append(tomorrow)
    return max(candidates)


def create_publish_job(
    *,
    source_kind: PublishSourceKind,
    source_id: int,
    brand: Brand,
    platform: SocialPlatform,
    account_id: int,
    body: str,
    target_url: str | None = None,
    payload_json: dict | None = None,
    scheduled_at: datetime,
    pete_override: bool = False,
    dry_run: bool = True,
) -> PublishJob:
    text = body.strip()
    if not text:
        raise ValueError("body is required")
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=UTC)
    with session_scope() as session:
        row = PublishJob(
            source_kind=source_kind,
            source_id=source_id,
            brand=brand,
            platform=platform,
            account_id=account_id,
            body=text,
            target_url=(target_url or "").strip() or None,
            payload_json=payload_json,
            scheduled_at=scheduled_at,
            status=PublishJobStatus.SCHEDULED,
            pete_override=pete_override,
            dry_run=dry_run,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return row


def mark_draft_status(draft_id: int, status: EngagementDraftStatus) -> None:
    with session_scope() as session:
        row = session.get(EngagementDraft, draft_id)
        if row is None:
            return
        row.status = status


def list_publish_jobs(
    *,
    brand: Brand | None = None,
    status: PublishJobStatus | None = None,
    limit: int = 200,
) -> list[PublishJob]:
    with session_scope() as session:
        stmt = (
            select(PublishJob)
            .options(selectinload(PublishJob.account))
            .order_by(PublishJob.scheduled_at.desc(), PublishJob.id.desc())
        )
        if brand is not None:
            stmt = stmt.where(PublishJob.brand == brand)
        if status is not None:
            stmt = stmt.where(PublishJob.status == status)
        stmt = stmt.limit(limit)
        return list(session.scalars(stmt))


def count_publish_jobs(
    *,
    status: PublishJobStatus | None = None,
) -> int:
    with session_scope() as session:
        stmt = select(func.count()).select_from(PublishJob)
        if status is not None:
            stmt = stmt.where(PublishJob.status == status)
        return int(session.scalar(stmt) or 0)


def claim_due_jobs(
    *,
    now: datetime | None = None,
    limit: int = 5,
) -> list[PublishJob]:
    """Mark due scheduled jobs as sending and return them."""
    reference = now or datetime.now(UTC)
    claimed: list[PublishJob] = []
    with session_scope() as session:
        stmt = (
            select(PublishJob)
            .options(selectinload(PublishJob.account))
            .where(
                PublishJob.status == PublishJobStatus.SCHEDULED,
                PublishJob.scheduled_at <= reference,
            )
            .order_by(PublishJob.scheduled_at.asc(), PublishJob.id.asc())
            .limit(limit)
        )
        stmt = with_row_lock(stmt, session)
        rows = list(session.scalars(stmt))
        for row in rows:
            row.status = PublishJobStatus.SENDING
            row.attempt_count = int(row.attempt_count or 0) + 1
            session.flush()
            session.refresh(row)
            # Detach account data for use after the session closes.
            _ = row.account
            claimed.append(row)
    return claimed


def mark_job_posted(
    job_id: int,
    *,
    posted_url: str | None,
    platform_post_id: str | None,
    now: datetime | None = None,
) -> None:
    reference = now or datetime.now(UTC)
    with session_scope() as session:
        row = session.get(PublishJob, job_id)
        if row is None:
            return
        row.status = PublishJobStatus.POSTED
        row.posted_url = (posted_url or "").strip() or None
        row.platform_post_id = (platform_post_id or "").strip() or None
        row.error = None
        account = session.get(SocialAccount, row.account_id)
        if account is not None:
            account.last_posted_at = reference
        if row.source_kind == PublishSourceKind.ENGAGEMENT_DRAFT:
            draft = session.get(EngagementDraft, row.source_id)
            if draft is not None:
                draft.status = EngagementDraftStatus.POSTED


def mark_job_failed(
    job_id: int,
    *,
    error: str,
    reschedule_at: datetime | None = None,
) -> None:
    with session_scope() as session:
        row = session.get(PublishJob, job_id)
        if row is None:
            return
        row.error = (error or "").strip()[:2000] or "unknown error"
        if reschedule_at is not None:
            if reschedule_at.tzinfo is None:
                reschedule_at = reschedule_at.replace(tzinfo=UTC)
            row.scheduled_at = reschedule_at
            row.status = PublishJobStatus.SCHEDULED
        else:
            row.status = PublishJobStatus.FAILED


def cancel_publish_job(job_id: int) -> PublishJob | None:
    with session_scope() as session:
        row = session.get(PublishJob, job_id)
        if row is None:
            return None
        if row.status in (PublishJobStatus.POSTED, PublishJobStatus.SENDING):
            return row
        row.status = PublishJobStatus.CANCELLED
        if row.source_kind == PublishSourceKind.ENGAGEMENT_DRAFT:
            draft = session.get(EngagementDraft, row.source_id)
            if draft is not None and draft.status == EngagementDraftStatus.SCHEDULED:
                draft.status = EngagementDraftStatus.APPROVED
        session.flush()
        session.refresh(row)
        return row


def thread_url_for_draft(draft_id: int) -> str | None:
    with session_scope() as session:
        draft = session.scalar(
            select(EngagementDraft)
            .options(selectinload(EngagementDraft.thread))
            .where(EngagementDraft.id == draft_id)
        )
        if draft is None or draft.thread is None:
            return None
        return draft.thread.url
