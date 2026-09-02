"""Tests for automatic verify_lead enqueue on contact upsert and idle seed."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from agent_crm.contacts.store import upsert_contact_profile
from agent_crm.db import session_scope
from agent_crm.enums import AgentJobKind, Brand
from agent_crm.jobs.store import count_pending_jobs
from agent_crm.models import AgentJob, Lead
from agent_crm.contacts.verifier import seed_verify_jobs_for_unverified

pytestmark = pytest.mark.usefixtures("db_url")


def test_upsert_enqueues_verify_job() -> None:
    upsert_contact_profile(
        email="jane.doe@novastudio.com",
        name="Jane Doe",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://novastudio.com/team",
    )
    assert count_pending_jobs(kind=AgentJobKind.VERIFY_LEAD) == 1

    with session_scope() as session:
        lead = session.scalar(
            select(Lead).where(Lead.email == "jane.doe@novastudio.com")
        )
        assert lead is not None
        job = session.scalar(
            select(AgentJob).where(AgentJob.kind == AgentJobKind.VERIFY_LEAD)
        )
        assert job is not None
        assert job.payload == {"lead_id": lead.id}


def test_upsert_verify_enqueue_is_idempotent() -> None:
    upsert_contact_profile(
        email="jane.doe@novastudio.com",
        name="Jane Doe",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://novastudio.com/team",
    )
    upsert_contact_profile(
        email="jane.doe@novastudio.com",
        name="Jane Doe",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://novastudio.com/about",
    )
    assert count_pending_jobs(kind=AgentJobKind.VERIFY_LEAD) == 1


def test_upsert_skips_verify_for_role_inbox() -> None:
    with pytest.raises(ValueError, match="rejected at ingest"):
        upsert_contact_profile(
            email="info@novastudio.com",
            name=None,
            brand=Brand.MIDNIGHTSATIN,
            source_url="https://novastudio.com/contact",
        )
    assert count_pending_jobs(kind=AgentJobKind.VERIFY_LEAD) == 0


def test_upsert_skips_verify_for_placeholder() -> None:
    with pytest.raises(ValueError, match="rejected at ingest"):
        upsert_contact_profile(
            email="name@domain.com",
            name=None,
            brand=Brand.MIDNIGHTSATIN,
            source_url="https://novastudio.com/template",
        )
    assert count_pending_jobs(kind=AgentJobKind.VERIFY_LEAD) == 0


def test_seed_verify_jobs_for_unverified() -> None:
    upsert_contact_profile(
        email="alice@novastudio.com",
        name="Alice",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://novastudio.com/a",
    )
    upsert_contact_profile(
        email="bob@novastudio.com",
        name="Bob",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://novastudio.com/b",
    )

    with session_scope() as session:
        rows = list(session.scalars(select(AgentJob)))
        for row in rows:
            session.delete(row)
        session.flush()

    assert count_pending_jobs(kind=AgentJobKind.VERIFY_LEAD) == 0
    seeded = seed_verify_jobs_for_unverified(limit=10)
    assert seeded == 2
    assert count_pending_jobs(kind=AgentJobKind.VERIFY_LEAD) == 2


def test_seed_skips_role_inboxes() -> None:
    from agent_crm.enums import ContactEmailKind, LeadSource, LeadStatus
    from agent_crm.models import ContactProfile, Lead

    with session_scope() as session:
        lead = Lead(
            email="support@novastudio.com",
            brand=Brand.MIDNIGHTSATIN,
            source=LeadSource.CONTACT,
            status=LeadStatus.NEW,
        )
        session.add(lead)
        session.flush()
        session.add(
            ContactProfile(
                email="support@novastudio.com",
                brand=Brand.MIDNIGHTSATIN,
                email_kind=ContactEmailKind.ROLE,
                source_urls=["https://novastudio.com/support"],
                lead_id=lead.id,
            )
        )

    seeded = seed_verify_jobs_for_unverified(limit=10)
    assert seeded == 0
