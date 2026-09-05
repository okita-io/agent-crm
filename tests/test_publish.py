"""Publisher layer: schedule, dry-run loop, Reddit URL parsing, Postiz adapter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from agent_crm.config import get_settings
from agent_crm.db import init_db, session_scope
from agent_crm.enums import (
    Brand,
    EngagementDraftStatus,
    EngagementThreadStatus,
    PublishJobStatus,
    PublishSourceKind,
    SocialPlatform,
)
from agent_crm.models import EngagementDraft, EngagementThread
from agent_crm.publish.adapters import (
    DryRunAdapter,
    PostizOwnedFeedAdapter,
    PublishAdapterError,
    RedditCommentAdapter,
    reddit_thing_id,
)
from agent_crm.publish.loop import PublishBudget, run_publish_loop
from agent_crm.publish.schedule import ScheduleError, schedule_engagement_drafts
from agent_crm.publish.store import (
    create_publish_job,
    create_social_account,
    list_publish_jobs,
    next_slot_for_account,
)


@pytest.fixture()
def pub_db(tmp_path, monkeypatch):
    db_path = tmp_path / "publish.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    # Reset engine so the new URL is used.
    import agent_crm.db as db_mod

    db_mod._engine = None
    db_mod._SessionFactory = None
    init_db()
    yield
    get_settings.cache_clear()
    db_mod._engine = None
    db_mod._SessionFactory = None


def _seed_draft(
    *,
    brand: Brand = Brand.CELESTIAL_NEXUS,
    text: str = "Helpful reply about charts.",
    url: str = "https://www.reddit.com/r/astrology/comments/abc123/title/",
) -> EngagementDraft:
    with session_scope() as session:
        thread = EngagementThread(
            url=url,
            brand=brand,
            title="Chart question",
            platform="reddit",
            popularity_score=80,
            status=EngagementThreadStatus.DRAFT_READY,
        )
        session.add(thread)
        session.flush()
        draft = EngagementDraft(
            thread_id=thread.id,
            brand=brand,
            draft_text=text,
            product_angle="natal chart",
            status=EngagementDraftStatus.DRAFT,
        )
        session.add(draft)
        session.flush()
        session.refresh(draft)
        return draft


def test_reddit_thing_id_submission_and_comment() -> None:
    assert (
        reddit_thing_id("https://www.reddit.com/r/test/comments/abc123/hello/")
        == "t3_abc123"
    )
    assert (
        reddit_thing_id(
            "https://www.reddit.com/r/test/comments/abc123/hello/def456/"
        )
        == "t1_def456"
    )
    with pytest.raises(PublishAdapterError):
        reddit_thing_id("https://example.com/not-reddit")


def test_schedule_and_dry_run_publish(pub_db, monkeypatch) -> None:
    monkeypatch.setenv("CRM_PUBLISH_DRY_RUN", "true")
    get_settings.cache_clear()
    draft = _seed_draft()
    account = create_social_account(
        brand=Brand.CELESTIAL_NEXUS,
        platform=SocialPlatform.REDDIT,
        handle="celestial_bot",
        daily_cap=3,
        min_interval_minutes=1,
    )
    jobs = schedule_engagement_drafts(
        draft_ids=[draft.id],
        account_id=account.id,
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        use_next_slot=False,
    )
    assert len(jobs) == 1
    assert jobs[0].status == PublishJobStatus.SCHEDULED
    assert jobs[0].dry_run is True

    result = run_publish_loop(budget=PublishBudget(max_jobs=5))
    assert result.claimed == 1
    assert result.posted == 1
    assert result.failed == 0

    posted = list_publish_jobs(status=PublishJobStatus.POSTED)
    assert len(posted) == 1
    assert posted[0].platform_post_id and posted[0].platform_post_id.startswith(
        "dry-run-"
    )


def test_tactic_studio_requires_pete_override(pub_db, monkeypatch) -> None:
    monkeypatch.setenv("CRM_PUBLISH_ALLOW_TACTIC_STUDIO", "false")
    get_settings.cache_clear()
    draft = _seed_draft(brand=Brand.TACTIC_STUDIO)
    account = create_social_account(
        brand=Brand.TACTIC_STUDIO,
        platform=SocialPlatform.REDDIT,
        handle="tactic_bot",
    )
    with pytest.raises(ScheduleError, match="tactic.studio"):
        schedule_engagement_drafts(
            draft_ids=[draft.id],
            account_id=account.id,
            pete_override=False,
        )
    jobs = schedule_engagement_drafts(
        draft_ids=[draft.id],
        account_id=account.id,
        pete_override=True,
        scheduled_at=datetime.now(UTC),
        use_next_slot=False,
    )
    assert len(jobs) == 1
    assert jobs[0].pete_override is True


def test_proof_gate_blocks_schedule(pub_db) -> None:
    draft = _seed_draft(text="We grew [NEED: metric] last quarter.")
    account = create_social_account(
        brand=Brand.CELESTIAL_NEXUS,
        platform=SocialPlatform.REDDIT,
        handle="celestial_bot",
    )
    with pytest.raises(ScheduleError, match=r"\[NEED:"):
        schedule_engagement_drafts(
            draft_ids=[draft.id],
            account_id=account.id,
        )


def test_next_slot_respects_interval(pub_db) -> None:
    account = create_social_account(
        brand=Brand.MIDNIGHTSATIN,
        platform=SocialPlatform.REDDIT,
        handle="ms_bot",
        min_interval_minutes=120,
        daily_cap=10,
    )
    with session_scope() as session:
        row = session.get(type(account), account.id)
        assert row is not None
        row.last_posted_at = datetime.now(UTC) - timedelta(minutes=30)
        session.flush()
        session.refresh(row)
        account = row
    slot = next_slot_for_account(account)
    assert slot > datetime.now(UTC)


def test_postiz_adapter_posts_payload(monkeypatch) -> None:
    monkeypatch.setenv("CRM_POSTIZ_BASE_URL", "http://postiz.test")
    monkeypatch.setenv("CRM_POSTIZ_API_KEY", "secret-key")
    get_settings.cache_clear()

    account = MagicMock()
    account.postiz_integration_id = "int-123"
    account.handle = "brand_x"

    job = MagicMock()
    job.body = "Owned feed post"
    job.platform = SocialPlatform.X
    job.scheduled_at = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    job.payload_json = None

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "post-9"}

    with patch("agent_crm.publish.adapters.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value = mock_response
        result = PostizOwnedFeedAdapter().publish(job, account)

    assert result.platform_post_id == "post-9"
    assert client.post.call_args[0][0] == "http://postiz.test/public/v1/posts"
    body = client.post.call_args.kwargs["json"]
    assert body["posts"][0]["integration"]["id"] == "int-123"
    assert body["posts"][0]["value"][0]["content"] == "Owned feed post"


def test_dry_run_adapter_logs(pub_db) -> None:
    account = create_social_account(
        brand=Brand.HEYBUDDY,
        platform=SocialPlatform.REDDIT,
        handle="hey_bot",
    )
    job = create_publish_job(
        source_kind=PublishSourceKind.ENGAGEMENT_DRAFT,
        source_id=1,
        brand=Brand.HEYBUDDY,
        platform=SocialPlatform.REDDIT,
        account_id=account.id,
        body="hello",
        target_url="https://www.reddit.com/r/test/comments/zzz/hi/",
        scheduled_at=datetime.now(UTC),
        dry_run=True,
    )
    outcome = DryRunAdapter().publish(job, account)
    assert outcome.dry_run is True
    assert outcome.platform_post_id == f"dry-run-{job.id}"


def test_reddit_adapter_requires_credentials(pub_db, monkeypatch) -> None:
    monkeypatch.setenv("CRM_PUBLISH_DRY_RUN", "false")
    monkeypatch.delenv("CRM_REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("CRM_REDDIT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("CRM_REDDIT_USERNAME", raising=False)
    monkeypatch.delenv("CRM_REDDIT_PASSWORD", raising=False)
    get_settings.cache_clear()
    account = create_social_account(
        brand=Brand.CELESTIAL_NEXUS,
        platform=SocialPlatform.REDDIT,
        handle="celestial_bot",
    )
    job = create_publish_job(
        source_kind=PublishSourceKind.ENGAGEMENT_DRAFT,
        source_id=1,
        brand=Brand.CELESTIAL_NEXUS,
        platform=SocialPlatform.REDDIT,
        account_id=account.id,
        body="hello",
        target_url="https://www.reddit.com/r/test/comments/abc/hi/",
        scheduled_at=datetime.now(UTC),
        dry_run=False,
    )
    with pytest.raises(PublishAdapterError, match="missing Reddit credentials"):
        RedditCommentAdapter().publish(job, account)
