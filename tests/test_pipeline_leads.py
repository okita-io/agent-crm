"""Tests for Pipeline & Leads visibility rules."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import select

from agent_crm.contacts.store import upsert_contact_profile
from agent_crm.db import session_scope
from agent_crm.enums import (
    Brand,
    ContactAudience,
    ContactKind,
    ContactVerificationStatus,
    LeadStatus,
    TopicalRelevanceVerdict,
)
from agent_crm.hunt.relevance import RelevanceAssessment
from agent_crm.models import ContactVerification, Lead
from agent_crm.contacts.pipeline_leads import (
    PIPELINE_LEAD_CSV_FIELDS,
    list_pipeline_leads,
    pipeline_leads_csv,
    pipeline_leads_export_filename,
)
from agent_crm.topic_relevance_store import upsert_url_topic_relevance
from agent_crm.contacts.verifier import record_immediate_invalid_email

pytestmark = pytest.mark.usefixtures("db_url")


def _mark_valid(session, lead_id: int, email: str) -> None:
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


def test_pipeline_hides_unverified_contact_lead() -> None:
    upsert_contact_profile(
        email="unverified@romanceblog.com",
        name="Reader",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://romanceblog.example/readers",
        audience=ContactAudience.END_USER,
    )
    assert list_pipeline_leads() == []


def test_pipeline_shows_valid_verified_lead() -> None:
    profile = upsert_contact_profile(
        email="verified@romanceblog.com",
        name="Reader",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://romanceblog.example/spicy-romance-readers",
        audience=ContactAudience.END_USER,
    )
    with session_scope() as session:
        lead = session.get(Lead, profile.lead_id)
        assert lead is not None
        _mark_valid(session, lead.id, "verified@romanceblog.com")

    leads = list_pipeline_leads()
    assert len(leads) == 1
    assert leads[0].email == "verified@romanceblog.com"


def test_pipeline_hides_invalid_verification() -> None:
    profile = upsert_contact_profile(
        email="reader@romanceblog.com",
        name="Reader",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://romanceblog.example/readers",
    )
    with session_scope() as session:
        lead = session.get(Lead, profile.lead_id)
        assert lead is not None
        record_immediate_invalid_email(
            lead_id=lead.id,
            email="reader@romanceblog.com",
            reasons=["manual invalid"],
        )
    assert list_pipeline_leads() == []


def test_pipeline_hides_off_topic_source_url() -> None:
    source_url = "https://developer.mozilla.org/en-US/docs/Web/API"
    profile = upsert_contact_profile(
        email="reader@romanceblog.com",
        name="Reader",
        brand=Brand.MIDNIGHTSATIN,
        source_url=source_url,
        audience=ContactAudience.END_USER,
    )
    with session_scope() as session:
        lead = session.get(Lead, profile.lead_id)
        assert lead is not None
        _mark_valid(session, lead.id, "reader@romanceblog.com")

    upsert_url_topic_relevance(
        url=source_url,
        brand=Brand.MIDNIGHTSATIN,
        assessment=RelevanceAssessment(
            verdict=TopicalRelevanceVerdict.OFF_TOPIC,
            reason="mozilla documentation",
        ),
    )
    assert list_pipeline_leads() == []


def test_pipeline_keeps_on_topic_romance_article_with_valid_email() -> None:
    source_url = "https://romanceblog.example/dark-romance-booktok-guide"
    profile = upsert_contact_profile(
        email="fan@romanceblog.com",
        name="Romance Fan",
        brand=Brand.MIDNIGHTSATIN,
        source_url=source_url,
        audience=ContactAudience.END_USER,
    )
    with session_scope() as session:
        lead = session.get(Lead, profile.lead_id)
        assert lead is not None
        _mark_valid(session, lead.id, "fan@romanceblog.com")

    upsert_url_topic_relevance(
        url=source_url,
        brand=Brand.MIDNIGHTSATIN,
        assessment=RelevanceAssessment(
            verdict=TopicalRelevanceVerdict.ON_TOPIC,
            reason="romance reader article",
        ),
    )
    leads = list_pipeline_leads(audience=ContactAudience.END_USER)
    assert len(leads) == 1
    assert leads[0].email == "fan@romanceblog.com"


def test_pipeline_filters_by_qualification() -> None:
    upsert_contact_profile(
        email="creator@social.com",
        name="Creator",
        brand=Brand.TACTIC_STUDIO,
        source_url="https://youtube.example/xr-reviewer",
        audience=ContactAudience.INFLUENCER,
    )
    upsert_contact_profile(
        email="jane.vega@brand.com",
        name="Marketing",
        brand=Brand.TACTIC_STUDIO,
        source_url="https://brand.example/press-kit",
        audience=ContactAudience.MARKETING,
    )
    with session_scope() as session:
        for email in ("creator@social.com", "jane.vega@brand.com"):
            lead = session.scalar(select(Lead).where(Lead.email == email))
            assert lead is not None
            for row in session.scalars(
                select(ContactVerification).where(ContactVerification.lead_id == lead.id)
            ):
                session.delete(row)
            session.flush()
            _mark_valid(session, lead.id, email)

    marketing_only = list_pipeline_leads(
        brand=Brand.TACTIC_STUDIO,
        audience=ContactAudience.MARKETING,
    )
    assert len(marketing_only) == 1
    assert marketing_only[0].email == "jane.vega@brand.com"


def test_pipeline_leads_full_export_includes_every_row() -> None:
    for index in range(3):
        profile = upsert_contact_profile(
            email=f"fan{index}@romanceblog.com",
            name=f"Fan {index}",
            brand=Brand.MIDNIGHTSATIN,
            source_url=f"https://romanceblog.example/readers/{index}",
            audience=ContactAudience.END_USER,
        )
        with session_scope() as session:
            lead = session.get(Lead, profile.lead_id)
            assert lead is not None
            _mark_valid(session, lead.id, f"fan{index}@romanceblog.com")

    assert len(list_pipeline_leads(limit=2)) == 2
    assert len(list_pipeline_leads(limit=None)) == 3

    payload, count = pipeline_leads_csv()
    assert count == 3
    text = payload.decode("utf-8-sig")
    assert text.splitlines()[0] == ",".join(PIPELINE_LEAD_CSV_FIELDS)
    assert "fan0@romanceblog.com" in text
    assert "fan1@romanceblog.com" in text
    assert "fan2@romanceblog.com" in text
    assert payload.startswith(b"\xef\xbb\xbf")
    assert (
        pipeline_leads_export_filename(
            brand=Brand.MIDNIGHTSATIN,
            audience=ContactAudience.END_USER,
        )
        == "pipeline-leads-midnightsatin-end_user.csv"
    )
