"""Tests for idle backlog seeding and junk contact ingest guards."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

from agent_crm.contact_quality import (
    is_junk_person_name,
    is_obviously_junk_email,
    prepare_contact_for_ingest,
)
from agent_crm.contact_store import (
    process_scraped_page_contacts,
    upsert_contact_profile,
)
from agent_crm.db import init_db, reset_engine, session_scope
from agent_crm.enums import AgentJobKind, Brand, ContactVerificationStatus, LeadStatus
from agent_crm.idle_backlog import seed_idle_backlog_jobs
from agent_crm.job_dispatcher import run_job_dispatcher
from agent_crm.job_store import count_pending_jobs
from agent_crm.models import ContactProfile, ContactVerification, Lead
from agent_crm.orchestrator import run_orchestrator_cycle
from agent_crm.verifier import check_email

pytestmark = pytest.mark.usefixtures("db_url")


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    db_path = tmp_path / "idle_junk.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    from agent_crm.config import get_settings

    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield f"sqlite:///{db_path}"
    reset_engine()
    get_settings.cache_clear()



def test_orchestrator_idle_tick_seeds_verify_jobs() -> None:
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
        from agent_crm.models import AgentJob

        for row in session.scalars(select(AgentJob)):
            session.delete(row)
        session.flush()

    assert count_pending_jobs(kind=AgentJobKind.VERIFY_LEAD) == 0
    run_orchestrator_cycle()
    assert count_pending_jobs(kind=AgentJobKind.VERIFY_LEAD) == 2


def test_seed_idle_backlog_prefers_verify_when_unverified() -> None:
    upsert_contact_profile(
        email="verify.me@studio.com",
        name="Verify Me",
        brand=Brand.TACTIC_STUDIO,
        source_url="https://studio.com/team",
    )
    with session_scope() as session:
        from agent_crm.models import AgentJob

        for row in session.scalars(select(AgentJob)):
            session.delete(row)
        session.flush()

    result = seed_idle_backlog_jobs(limit=5)
    assert result["verify"] == 1
    assert result["enrich"] == 0


def test_job_dispatcher_idle_seeds_and_drains_without_cli() -> None:
    upsert_contact_profile(
        email="idle.worker@studio.com",
        name="Idle Worker",
        brand=Brand.TACTIC_STUDIO,
        source_url="https://studio.com/team",
    )

    with session_scope() as session:
        from agent_crm.models import AgentJob

        for row in session.scalars(select(AgentJob)):
            session.delete(row)
        session.flush()

    calls: list[int] = []

    def _fake_verify(lead_id: int) -> None:
        calls.append(lead_id)

    def _stop_after_work(_seconds: float) -> None:
        if calls:
            raise StopIteration

    with (
        patch("agent_crm.job_dispatcher.verify_lead", side_effect=_fake_verify),
        patch("agent_crm.job_dispatcher.time.sleep", side_effect=_stop_after_work),
    ):
        with pytest.raises(StopIteration):
            run_job_dispatcher(batch_size=5, poll_seconds=60)

    assert len(calls) == 1


@pytest.mark.parametrize(
    "name",
    [
        "Screen Shot",
        "Screenshot 2024-01-01",
        "screenshot.png",
        "IMG_1234",
        "photo-001.jpg",
    ],
)
def test_junk_person_names(name: str) -> None:
    assert is_junk_person_name(name)


def test_screenshot_filename_names_not_stored_as_leads() -> None:
    profiles = process_scraped_page_contacts(
        markdown=(
            "Contact Screen Shot <screenshot.png@cdn.example.com> or "
            "Jane Doe <jane@studio.com>"
        ),
        source_url="https://studio.com/contact",
        brand=Brand.TACTIC_STUDIO,
    )
    assert len(profiles) == 1
    assert profiles[0].email == "jane@studio.com"

    with session_scope() as session:
        rows = list(session.scalars(select(ContactProfile)))
        assert len(rows) == 1
        assert rows[0].email == "jane@studio.com"
        assert rows[0].name == "Jane Doe"


def test_filename_email_rejected_at_ingest() -> None:
    assert is_obviously_junk_email("logo@cdn.png")
    assert prepare_contact_for_ingest("logo@cdn.png", "Logo") is None

    with pytest.raises(ValueError, match="rejected at ingest"):
        upsert_contact_profile(
            email="screenshot.png@files.example.com",
            name="Screen Shot",
            brand=Brand.TACTIC_STUDIO,
            source_url="https://studio.com/contact",
        )


def test_role_and_placeholder_still_invalid_in_verifier() -> None:
    role = check_email("info@agency.com")
    placeholder = check_email("name@domain.com")
    assert role.status == ContactVerificationStatus.INVALID
    assert placeholder.status == ContactVerificationStatus.INVALID
    assert any("role" in reason for reason in role.reasons)
    assert any("placeholder" in reason for reason in placeholder.reasons)


def test_role_inbox_marked_invalid_at_ingest() -> None:
    upsert_contact_profile(
        email="hello@studio.com",
        name=None,
        brand=Brand.TACTIC_STUDIO,
        source_url="https://studio.com/contact",
    )

    with session_scope() as session:
        lead = session.scalar(select(Lead).where(Lead.email == "hello@studio.com"))
        assert lead is not None
        assert lead.status == LeadStatus.DISQUALIFIED
        verification = session.scalar(
            select(ContactVerification).where(ContactVerification.lead_id == lead.id)
        )
        assert verification is not None
        assert verification.status == ContactVerificationStatus.INVALID

    assert count_pending_jobs(kind=AgentJobKind.VERIFY_LEAD) == 0


def test_orchestrator_idle_seed_runs_on_every_cycle() -> None:
    with patch("agent_crm.orchestrator.seed_idle_backlog_jobs") as mock_seed:
        mock_seed.return_value = {"verify": 0, "enrich": 0}
        run_orchestrator_cycle()
    mock_seed.assert_called_once()
