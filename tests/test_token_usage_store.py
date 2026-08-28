"""Persisted LLM token totals and hourly average rate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from agent_crm.models import LlmTokenUsage, LlmTokenUsageHour
from agent_crm.token_usage_store import (
    load_token_usage_snapshot,
    merge_usage_snapshots,
    record_token_usage,
    tokens_per_hour,
    utc_hour_start,
)


def test_tokens_per_hour_uses_elapsed_wall_clock() -> None:
    first_seen = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    assert tokens_per_hour(50_000, 10_000, first_seen, now=now) == 30_000.0


def test_tokens_per_hour_floors_sub_minute_elapsed() -> None:
    first_seen = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    now = datetime(2026, 8, 28, 12, 0, 1, tzinfo=UTC)
    # 1 second would otherwise look like millions/hr; floor at 1 minute.
    assert tokens_per_hour(1_000, 0, first_seen, now=now) == 60_000.0


def test_record_token_usage_persists_lifetime_and_hour_bucket(db_url) -> None:
    now = datetime(2026, 8, 28, 15, 10, tzinfo=UTC)
    record_token_usage("research", 1000, 200, now=now)
    record_token_usage("research", 500, 50, now=now + timedelta(minutes=5))
    record_token_usage("hermes", 100, 0, estimated=True, now=now)

    snapshot = load_token_usage_snapshot(now=now + timedelta(hours=1))
    assert snapshot["by_actor"]["research"]["prompt_tokens"] == 1500
    assert snapshot["by_actor"]["research"]["completion_tokens"] == 250
    assert snapshot["by_actor"]["research"]["requests"] == 2
    assert snapshot["by_actor"]["hermes"]["estimated_requests"] == 1
    assert snapshot["totals"]["prompt_tokens"] == 1600

    from agent_crm.db import session_scope

    hour = utc_hour_start(now)
    with session_scope() as session:
        lifetime = session.get(LlmTokenUsage, "research")
        assert lifetime is not None
        assert lifetime.prompt_tokens == 1500
        bucket = session.get(LlmTokenUsageHour, ("research", hour))
        assert bucket is not None
        assert bucket.prompt_tokens == 1500
        hours = session.scalars(select(LlmTokenUsageHour)).all()
        assert len(hours) == 2


def test_merge_usage_snapshots_keeps_higher_counts_and_earlier_first_seen() -> None:
    earlier = datetime(2026, 8, 1, tzinfo=UTC)
    later = datetime(2026, 8, 28, tzinfo=UTC)
    merged = merge_usage_snapshots(
        {
            "by_actor": {
                "research": {
                    "prompt_tokens": 1_000_000,
                    "completion_tokens": 10,
                    "first_seen_at": earlier.isoformat(),
                }
            }
        },
        {
            "by_actor": {
                "research": {
                    "prompt_tokens": 5_000,
                    "completion_tokens": 80,
                    "first_seen_at": later.isoformat(),
                }
            }
        },
    )
    row = merged["by_actor"]["research"]
    assert row["prompt_tokens"] == 1_000_000
    assert row["completion_tokens"] == 80
    assert row["first_seen_at"] == earlier.isoformat()
