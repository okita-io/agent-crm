"""Tests for agent job queue and furthest-behind dispatcher scheduling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from agent_crm.db import session_scope
from agent_crm.enums import AgentJobKind, AgentJobStatus
from agent_crm.job_store import (
    claim_jobs,
    claim_non_spark_jobs,
    claim_spark_jobs,
    enqueue_enrich_contact_job,
    pick_furthest_behind_kind,
    pending_kind_lag_metrics,
)
from agent_crm.models import AgentJob
from sqlalchemy import select

pytestmark = pytest.mark.usefixtures("db_url")


def _enqueue_kind(kind: AgentJobKind, dedupe_suffix: str, created_at: datetime) -> None:
    with session_scope() as session:
        session.add(
            AgentJob(
                kind=kind,
                status=AgentJobStatus.PENDING,
                payload={"test": dedupe_suffix},
                priority=50,
                dedupe_key=f"{kind.value}:{dedupe_suffix}",
                created_at=created_at,
                updated_at=created_at,
            )
        )


def test_claim_prefers_enrich_when_enrich_backlog_is_larger() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(200):
        _enqueue_kind(
            AgentJobKind.ENRICH_CONTACT,
            f"enrich-{index}",
            base + timedelta(seconds=index),
        )
    for index in range(5):
        _enqueue_kind(
            AgentJobKind.VERIFY_LEAD,
            f"verify-{index}",
            base + timedelta(seconds=index),
        )

    with patch("agent_crm.job_store.spark_queue_has_capacity", return_value=True):
        from agent_crm.job_store import claim_spark_jobs

        claimed = claim_spark_jobs(max_claim=1)
    assert len(claimed) == 1
    assert claimed[0].kind == AgentJobKind.ENRICH_CONTACT


def test_claim_verify_when_enrich_queue_empty() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(5):
        _enqueue_kind(
            AgentJobKind.VERIFY_LEAD,
            f"verify-{index}",
            base + timedelta(seconds=index),
        )

    with patch("agent_crm.job_store.spark_queue_has_capacity", return_value=True):
        claimed = claim_jobs(max_claim=3)
    assert len(claimed) == 3
    assert all(job.kind == AgentJobKind.VERIFY_LEAD for job in claimed)


def test_claim_switches_to_verify_when_verify_lag_is_worse() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(10):
        _enqueue_kind(
            AgentJobKind.ENRICH_CONTACT,
            f"enrich-{index}",
            base + timedelta(seconds=index),
        )
    for index in range(8):
        _enqueue_kind(
            AgentJobKind.VERIFY_LEAD,
            f"verify-{index}",
            base + timedelta(hours=1, seconds=index),
        )

    with patch("agent_crm.job_store.spark_queue_has_capacity", return_value=True):
        first_batch = claim_jobs(max_claim=3)
    assert len(first_batch) == 3
    assert all(job.kind == AgentJobKind.VERIFY_LEAD for job in first_batch)

    with patch("agent_crm.job_store.spark_queue_has_capacity", return_value=True):
        next_claim = claim_jobs(max_claim=1)
    assert len(next_claim) == 1
    assert next_claim[0].kind == AgentJobKind.VERIFY_LEAD


def test_verify_claimed_when_spark_full_and_enrich_pending() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(50):
        _enqueue_kind(
            AgentJobKind.ENRICH_CONTACT,
            f"enrich-{index}",
            base + timedelta(seconds=index),
        )
    for index in range(3):
        _enqueue_kind(
            AgentJobKind.VERIFY_LEAD,
            f"verify-{index}",
            base + timedelta(seconds=index),
        )

    with patch("agent_crm.job_store.spark_queue_has_capacity", return_value=False):
        claimed = claim_jobs(max_claim=2)
    assert len(claimed) == 2
    assert all(job.kind == AgentJobKind.VERIFY_LEAD for job in claimed)


def test_claim_non_spark_jobs_skips_enrich_backlog() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(20):
        _enqueue_kind(
            AgentJobKind.ENRICH_CONTACT,
            f"enrich-{index}",
            base + timedelta(seconds=index),
        )
    for index in range(4):
        _enqueue_kind(
            AgentJobKind.VERIFY_LEAD,
            f"verify-{index}",
            base + timedelta(hours=1, seconds=index),
        )

    claimed = claim_non_spark_jobs(max_claim=10)
    assert len(claimed) == 4
    assert all(job.kind == AgentJobKind.VERIFY_LEAD for job in claimed)


def test_claim_jobs_prioritizes_non_spark_before_spark() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(10):
        _enqueue_kind(
            AgentJobKind.ENRICH_CONTACT,
            f"enrich-{index}",
            base + timedelta(seconds=index),
        )
    for index in range(3):
        _enqueue_kind(
            AgentJobKind.VERIFY_LEAD,
            f"verify-{index}",
            base + timedelta(seconds=index),
        )

    with patch("agent_crm.job_store.spark_queue_has_capacity", return_value=True):
        claimed = claim_jobs(max_claim=5)
    assert len(claimed) == 5
    assert claimed[0].kind == AgentJobKind.VERIFY_LEAD
    assert claimed[1].kind == AgentJobKind.VERIFY_LEAD
    assert claimed[2].kind == AgentJobKind.VERIFY_LEAD
    assert all(job.kind == AgentJobKind.ENRICH_CONTACT for job in claimed[3:])


def test_enqueue_on_upsert_is_idempotent() -> None:
    assert enqueue_enrich_contact_job(42) is True
    assert enqueue_enrich_contact_job(42) is False

    with session_scope() as session:
        rows = list(session.scalars(select(AgentJob)))
        assert len(rows) == 1
        assert rows[0].kind == AgentJobKind.ENRICH_CONTACT
        assert rows[0].payload == {"contact_profile_id": 42}


def test_spark_claim_stops_at_four_running() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(10):
        _enqueue_kind(
            AgentJobKind.ENRICH_CONTACT,
            f"enrich-{index}",
            base + timedelta(seconds=index),
        )

    with (
        patch("agent_crm.job_store.spark_queue_has_capacity", return_value=True),
        patch("agent_crm.job_store.count_running_jobs", return_value=4),
    ):
        claimed = claim_jobs(max_claim=5)
    assert claimed == []
