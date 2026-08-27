"""Tests for topical relevance backfill jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from agent_crm.contact_store import upsert_contact_profile
from agent_crm.db import session_scope
from agent_crm.enums import (
    AgentJobKind,
    Brand,
    ContactAudience,
    ContactKind,
    ContactVerificationStatus,
    LeadStatus,
    TopicalRelevanceVerdict,
)
from agent_crm.hunt_relevance import RelevanceAssessment
from agent_crm.idle_backlog import seed_idle_backlog_jobs
from agent_crm.job_dispatcher import execute_job
from agent_crm.job_store import count_pending_jobs, enqueue_topical_relevance_job
from agent_crm.models import AgentJob, ContactVerification, Lead, UrlTopicRelevance
from agent_crm.pipeline_leads import list_pipeline_leads
from agent_crm.topic_relevance_store import (
    check_topical_relevance_job,
    seed_topical_relevance_jobs,
    upsert_url_topic_relevance,
)

pytestmark = pytest.mark.usefixtures("db_url")


def _mark_valid(lead_id: int, email: str) -> None:
    with session_scope() as session:
        session.add(
            ContactVerification(
                lead_id=lead_id,
                contact=email,
                contact_kind=ContactKind.EMAIL,
                status=ContactVerificationStatus.VALID,
                reasons=["test valid"],
                checked_at=datetime.now(UTC),
            )
        )


def test_backfill_mozilla_url_marked_off_topic_and_hidden() -> None:
    mozilla_url = "https://developer.mozilla.org/en-US/docs/Web/API"
    profile = upsert_contact_profile(
        email="reader@romanceblog.com",
        name="Reader",
        brand=Brand.MIDNIGHTSATIN,
        source_url=mozilla_url,
        audience=ContactAudience.END_USER,
    )
    with session_scope() as session:
        lead = session.get(Lead, profile.lead_id)
        assert lead is not None
        _mark_valid(lead.id, "reader@romanceblog.com")

    with patch(
        "agent_crm.topic_relevance_store.fetch_public_page_excerpt",
        return_value=("MDN Web Docs", "web platform documentation reference", 200),
    ):
        assessment = check_topical_relevance_job(
            url=mozilla_url,
            brand=Brand.MIDNIGHTSATIN,
            source_kind="contact_profile",
            source_id=profile.id,
            allow_spark=False,
        )

    assert assessment.verdict == TopicalRelevanceVerdict.OFF_TOPIC
    assert list_pipeline_leads() == []

    with session_scope() as session:
        lead = session.get(Lead, profile.lead_id)
        assert lead is not None
        assert lead.status == LeadStatus.DISQUALIFIED
        row = session.scalar(
            select(UrlTopicRelevance).where(
                UrlTopicRelevance.url == mozilla_url,
                UrlTopicRelevance.brand == Brand.MIDNIGHTSATIN,
            )
        )
        assert row is not None
        assert row.verdict == TopicalRelevanceVerdict.OFF_TOPIC


def test_backfill_romance_article_stays_on_pipeline_when_valid() -> None:
    romance_url = "https://romanceblog.example/dark-romance-booktok-guide"
    profile = upsert_contact_profile(
        email="fan@romanceblog.com",
        name="Fan",
        brand=Brand.MIDNIGHTSATIN,
        source_url=romance_url,
        audience=ContactAudience.END_USER,
    )
    with session_scope() as session:
        lead = session.get(Lead, profile.lead_id)
        assert lead is not None
        _mark_valid(lead.id, "fan@romanceblog.com")

    with patch(
        "agent_crm.topic_relevance_store.fetch_public_page_excerpt",
        return_value=(
            "Dark Romance BookTok Guide",
            "romance readers discuss spicy romance and booktok communities",
            200,
        ),
    ):
        assessment = check_topical_relevance_job(
            url=romance_url,
            brand=Brand.MIDNIGHTSATIN,
            allow_spark=False,
        )

    assert assessment.verdict == TopicalRelevanceVerdict.ON_TOPIC
    leads = list_pipeline_leads()
    assert len(leads) == 1
    assert leads[0].email == "fan@romanceblog.com"


def test_idle_tick_seeds_topical_relevance_job() -> None:
    upsert_contact_profile(
        email="pending@example.com",
        name="Pending",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://romanceblog.example/pending-thread",
    )
    with session_scope() as session:
        for row in session.scalars(select(AgentJob)):
            session.delete(row)

    with patch("agent_crm.idle_backlog.count_unverified_email_leads", return_value=0), patch(
        "agent_crm.idle_backlog.count_unenriched_person_profiles", return_value=0
    ), patch("agent_crm.idle_backlog.count_unqualified_contacts", return_value=0):
        result = seed_idle_backlog_jobs(limit=5)

    assert result["topical"] >= 1
    assert count_pending_jobs(kind=AgentJobKind.CHECK_TOPICAL_RELEVANCE) >= 1


def test_dispatcher_executes_topical_relevance_job() -> None:
    url = "https://developer.mozilla.org/en-US/docs/Web/API"
    upsert_contact_profile(
        email="hidden@example.com",
        name="Hidden",
        brand=Brand.MIDNIGHTSATIN,
        source_url=url,
    )
    assert enqueue_topical_relevance_job(
        url=url,
        brand=Brand.MIDNIGHTSATIN,
        source_kind="contact_profile",
        source_id=1,
    )

    with session_scope() as session:
        job = session.scalar(
            select(AgentJob).where(
                AgentJob.kind == AgentJobKind.CHECK_TOPICAL_RELEVANCE
            )
        )
        assert job is not None

    with patch(
        "agent_crm.topic_relevance_store.fetch_public_page_excerpt",
        return_value=("MDN", "documentation", 200),
    ):
        execute_job(job.id, job.kind, job.payload)

    with session_scope() as session:
        row = session.scalar(
            select(UrlTopicRelevance).where(
                UrlTopicRelevance.url == url,
                UrlTopicRelevance.brand == Brand.MIDNIGHTSATIN,
            )
        )
        assert row is not None
        assert row.verdict == TopicalRelevanceVerdict.OFF_TOPIC
