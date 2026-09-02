"""Tests for contact qualification at ingest and backfill."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

from agent_crm.contacts.comment_extractor import extract_comment_people
from agent_crm.contacts.qualification import (
    infer_audience_from_ingest,
    is_weakly_qualified,
    seed_qualify_jobs_for_unqualified,
)
from agent_crm.contacts.store import process_scraped_page_contacts, upsert_contact_profile
from agent_crm.db import session_scope
from agent_crm.enums import AgentJobKind, Brand, ContactAudience
from agent_crm.jobs.idle_backlog import seed_idle_backlog_jobs
from agent_crm.jobs.store import count_pending_jobs
from agent_crm.models import AgentJob, Lead

pytestmark = pytest.mark.usefixtures("db_url")


def test_comment_person_classified_as_end_user() -> None:
    people = extract_comment_people(
        markdown="Comments\n\nu/romancefan said: I loved this spicy romance chapter!",
        source_url="https://reddit.com/r/romancebooks/thread",
    )
    assert people
    assert people[0].audience == ContactAudience.END_USER


def test_influencer_comment_snippet_upgrades_audience() -> None:
    people = extract_comment_people(
        markdown=(
            "Comments\n\nu/xrcreator said: check out my YouTube channel "
            "for WebAR reviews"
        ),
        source_url="https://reddit.com/r/augmentedreality/thread",
    )
    assert people
    assert people[0].audience == ContactAudience.INFLUENCER


def test_marketing_contact_from_press_page() -> None:
    audience = infer_audience_from_ingest(
        source_url="https://brandstudio.com/press-kit",
        email="press@brandstudio.com",
        name="Brand Media",
    )
    assert audience == ContactAudience.MARKETING


def test_marketing_contact_from_vp_title() -> None:
    audience = infer_audience_from_ingest(
        source_url="https://grocery.example/leadership/jane-smith",
        name="Jane Smith, VP of Marketing",
    )
    assert audience == ContactAudience.MARKETING


def test_b2b_contact_from_team_page() -> None:
    audience = infer_audience_from_ingest(
        source_url="https://industrial-corp.com/solutions/enterprise-ar",
        email="jane.doe@industrial-corp.com",
        name="Enterprise Sales",
    )
    assert audience == ContactAudience.B2B


def test_process_scraped_page_sets_qualification() -> None:
    profiles = process_scraped_page_contacts(
        markdown="Reach jane.doe@industrial-corp.com from our enterprise AR solutions team page.",
        source_url="https://industrial-corp.com/solutions/enterprise-ar",
        brand=Brand.TACTIC_STUDIO,
        audience=ContactAudience.B2B,
    )
    assert len(profiles) == 1
    assert profiles[0].audience == ContactAudience.B2B


def test_unqualified_contact_gets_qualify_job_on_idle_tick() -> None:
    upsert_contact_profile(
        email="mystery@forum-site.io",
        name=None,
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://forum.example/thread",
        audience=None,
    )
    with session_scope() as session:
        for row in session.scalars(select(AgentJob)):
            session.delete(row)

    assert is_weakly_qualified(None) is True
    seeded = seed_qualify_jobs_for_unqualified(limit=5)
    assert seeded == 1
    assert count_pending_jobs(kind=AgentJobKind.QUALIFY_CONTACT) == 1


def test_idle_backlog_seeds_qualify_jobs() -> None:
    upsert_contact_profile(
        email="weak@forum-site.io",
        name="Commenter",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://reddit.com/r/romancebooks/comments/abc",
        audience=ContactAudience.END_USER,
    )
    with session_scope() as session:
        for row in session.scalars(select(AgentJob)):
            session.delete(row)

    with patch("agent_crm.jobs.idle_backlog.count_unverified_email_leads", return_value=0), patch(
        "agent_crm.jobs.idle_backlog.count_unenriched_person_profiles", return_value=0
    ), patch("agent_crm.jobs.idle_backlog.count_urls_needing_topical_check", return_value=0):
        result = seed_idle_backlog_jobs(limit=5)

    assert result["qualify"] >= 1


def test_spark_qualify_promo_media() -> None:
    from agent_crm.contacts.qualification import qualify_contact_profile

    profile = upsert_contact_profile(
        email="jane.smith@brandstudio.com",
        name="Jane Smith",
        brand=Brand.TACTIC_STUDIO,
        source_url="https://brandstudio.com/press-kit",
        audience=ContactAudience.END_USER,
    )
    with patch("agent_crm.contacts.qualification.search", return_value=[]), patch(
        "agent_crm.contacts.qualification.chat_completions"
    ) as mock_llm:
        mock_llm.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"audience":"marketing","evidence":["press kit page"],'
                            '"public_email":null}'
                        )
                    }
                }
            ]
        }
        result = qualify_contact_profile(profile.id, allow_spark=True)

    assert result is not None
    assert result.audience == ContactAudience.MARKETING

    with session_scope() as session:
        lead = session.scalar(
            select(Lead).where(Lead.email == "jane.smith@brandstudio.com")
        )
        assert lead is not None
        assert lead.audience == ContactAudience.MARKETING


def test_spark_discovered_email_rejects_role_and_placeholder() -> None:
    from agent_crm.contacts.qualification import (
        QualificationResult,
        _persist_qualification_on_profile,
        _spark_qualification,
    )

    profile = upsert_contact_profile(
        email="handle-only-placeholder@forum-site.io",
        name="Mystery",
        brand=Brand.TACTIC_STUDIO,
        source_url="https://forum-site.io/u/mystery",
        audience=ContactAudience.END_USER,
    )
    with session_scope() as session:
        lead = session.get(Lead, profile.lead_id)
        assert lead is not None
        lead.email = None
        session.flush()

    with patch("agent_crm.contacts.qualification.chat_completions") as mock_llm:
        mock_llm.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"audience":"end_user","evidence":["forum"],'
                            '"public_email":"info@agency.com"}'
                        )
                    }
                }
            ]
        }
        spark_result = _spark_qualification(
            email=None,
            name="Mystery",
            handle="mystery",
            platform="reddit",
            serp_snippets=[],
        )
    assert spark_result is not None
    assert spark_result.discovered_email is None

    with patch("agent_crm.contacts.qualification.chat_completions") as mock_llm:
        mock_llm.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"audience":"end_user","evidence":["forum"],'
                            '"public_email":"name@domain.com"}'
                        )
                    }
                }
            ]
        }
        spark_result = _spark_qualification(
            email=None,
            name="Mystery",
            handle="mystery",
            platform="reddit",
            serp_snippets=[],
        )
    assert spark_result is not None
    assert spark_result.discovered_email is None

    _persist_qualification_on_profile(
        profile.id,
        QualificationResult(
            audience=ContactAudience.END_USER,
            evidence=["forced"],
            confidence="spark",
            discovered_email="info@agency.com",
            spark_used=True,
        ),
    )
    with session_scope() as session:
        lead = session.get(Lead, profile.lead_id)
        assert lead is not None
        assert lead.email is None
