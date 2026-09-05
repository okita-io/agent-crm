"""Schedule helpers: approve engagement drafts into publish_jobs."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_crm.config import get_settings
from agent_crm.enums import (
    Brand,
    EngagementDraftStatus,
    PublishSourceKind,
    SocialPlatform,
)
from agent_crm.models import PublishJob, SocialAccount
from agent_crm.publish.store import (
    create_publish_job,
    get_engagement_draft,
    get_social_account,
    mark_draft_status,
    next_slot_for_account,
    thread_url_for_draft,
)


class ScheduleError(Exception):
    """Human-facing schedule rejection."""


def schedule_engagement_drafts(
    *,
    draft_ids: list[int],
    account_id: int,
    scheduled_at: datetime | None = None,
    use_next_slot: bool = True,
    pete_override: bool = False,
    dry_run: bool | None = None,
) -> list[PublishJob]:
    """Approve drafts and create scheduled publish jobs."""
    if not draft_ids:
        raise ScheduleError("draft_ids is required")
    account = get_social_account(account_id)
    if account is None:
        raise ScheduleError(f"social account {account_id} not found")
    if not account.enabled:
        raise ScheduleError(f"social account {account_id} is disabled")

    settings = get_settings()
    effective_dry_run = settings.publish_dry_run if dry_run is None else dry_run
    jobs: list[PublishJob] = []
    slot = scheduled_at
    if slot is not None and slot.tzinfo is None:
        slot = slot.replace(tzinfo=UTC)

    for draft_id in draft_ids:
        draft = get_engagement_draft(draft_id)
        if draft is None:
            raise ScheduleError(f"draft {draft_id} not found")
        if draft.status == EngagementDraftStatus.REJECTED:
            raise ScheduleError(f"draft {draft_id} is rejected")
        if draft.status == EngagementDraftStatus.POSTED:
            raise ScheduleError(f"draft {draft_id} already posted")
        if "[NEED:" in draft.draft_text:
            raise ScheduleError(
                f"draft {draft_id} still contains [NEED: …] proof placeholders"
            )
        if draft.brand == Brand.TACTIC_STUDIO and not (
            pete_override or settings.publish_allow_tactic_studio
        ):
            raise ScheduleError(
                "tactic.studio publish requires pete_override or "
                "CRM_PUBLISH_ALLOW_TACTIC_STUDIO"
            )
        from agent_crm.projects.channel_flags import active_brands_for

        if draft.brand not in active_brands_for("publish"):
            raise ScheduleError(
                f"publish channel is disarmed for project {draft.brand.value}"
            )
        if account.brand != draft.brand:
            raise ScheduleError(
                f"account brand {account.brand.value} does not match draft "
                f"{draft_id} brand {draft.brand.value}"
            )
        if account.platform != SocialPlatform.REDDIT and draft.thread is not None:
            # Engagement replies are Reddit/forum comments; owned feeds use content packages.
            pass

        when = slot
        if when is None or use_next_slot:
            when = next_slot_for_account(account, now=slot or datetime.now(UTC))
            # Stagger subsequent drafts in the same batch.
            slot = when

        target_url = thread_url_for_draft(draft_id)
        if account.platform == SocialPlatform.REDDIT and not target_url:
            raise ScheduleError(f"draft {draft_id} has no thread URL")

        job = create_publish_job(
            source_kind=PublishSourceKind.ENGAGEMENT_DRAFT,
            source_id=draft_id,
            brand=draft.brand,
            platform=account.platform,
            account_id=account.id,
            body=draft.draft_text,
            target_url=target_url,
            scheduled_at=when,
            pete_override=pete_override,
            dry_run=effective_dry_run,
        )
        mark_draft_status(draft_id, EngagementDraftStatus.SCHEDULED)
        jobs.append(job)
        # Push next draft after this one's slot + account interval.
        from datetime import timedelta

        slot = when + timedelta(minutes=account.min_interval_minutes)

    return jobs


def schedule_content_package(
    *,
    source_id: int,
    brand: Brand,
    account: SocialAccount,
    body: str,
    scheduled_at: datetime | None = None,
    use_next_slot: bool = True,
    payload_json: dict | None = None,
    pete_override: bool = False,
    dry_run: bool | None = None,
) -> PublishJob:
    """Schedule an owned-feed package (content-loop / manual)."""
    if brand == Brand.TACTIC_STUDIO and not (
        pete_override or get_settings().publish_allow_tactic_studio
    ):
        raise ScheduleError(
            "tactic.studio publish requires pete_override or "
            "CRM_PUBLISH_ALLOW_TACTIC_STUDIO"
        )
    from agent_crm.projects.channel_flags import active_brands_for

    if brand not in active_brands_for("publish"):
        raise ScheduleError(f"publish channel is disarmed for project {brand.value}")
    if "[NEED:" in body:
        raise ScheduleError("body still contains [NEED: …] proof placeholders")
    if account.brand != brand:
        raise ScheduleError("account brand does not match package brand")
    settings = get_settings()
    effective_dry_run = settings.publish_dry_run if dry_run is None else dry_run
    when = scheduled_at
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    if when is None or use_next_slot:
        when = next_slot_for_account(account, now=when or datetime.now(UTC))
    return create_publish_job(
        source_kind=PublishSourceKind.CONTENT_PACKAGE,
        source_id=source_id,
        brand=brand,
        platform=account.platform,
        account_id=account.id,
        body=body,
        target_url=None,
        payload_json=payload_json,
        scheduled_at=when,
        pete_override=pete_override,
        dry_run=effective_dry_run,
    )
