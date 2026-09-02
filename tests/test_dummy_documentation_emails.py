"""Tests for documentation dummy emails (nowhere@mozilla.org, mailto garbage)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.contacts.extractor import extract_contacts, is_skipped_email, normalize_email
from agent_crm.contacts.quality import (
    is_dummy_documentation_email,
    is_person_email,
    is_placeholder_email,
)
from agent_crm.contacts.store import backfill_contact_quality, upsert_contact_profile
from agent_crm.db import init_db, reset_engine, session_scope
from agent_crm.enums import Brand, ContactEmailKind, ContactVerificationStatus, LeadSource, LeadStatus
from agent_crm.models import ContactProfile, Lead
from agent_crm.contacts.verifier import check_email
from sqlalchemy import select


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    db_path = tmp_path / "dummy_emails.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    from agent_crm.config import get_settings

    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield f"sqlite:///{db_path}"
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture()
def api_client(db_url):
    yield TestClient(app)


class FakeMx:
    def __init__(self, exchange: str, preference: int) -> None:
        self.exchange = exchange
        self.preference = preference


class FakeResolver:
    def __init__(self, records: dict[tuple[str, str], list]) -> None:
        self._records = records

    def resolve(self, qname: str, rdtype: str) -> list:
        key = (qname.lower(), rdtype.upper())
        return self._records[key]


def _mozilla_mx_resolver() -> FakeResolver:
    return FakeResolver(
        {
            ("mozilla.org", "A"): ["192.0.2.1"],
            ("mozilla.org", "MX"): [FakeMx("mail.mozilla.org.", 10)],
        }
    )


@pytest.mark.parametrize(
    "raw",
    [
        "nowhere@mozilla.org",
        "nobody@mozilla.org",
        "borderify@mozilla.org",
        "beastify@mozilla.org",
    ],
)
def test_dummy_locals_are_placeholder_and_skipped(raw: str) -> None:
    assert is_dummy_documentation_email(raw)
    assert is_placeholder_email(raw)
    assert is_skipped_email(raw)
    assert not is_person_email(raw, name="Send email to nowhere")


def test_normalize_email_strips_mailto_query_strings() -> None:
    assert normalize_email("nowhere@mozilla.org?cc=nobody@mozilla.org") == "nowhere@mozilla.org"
    assert normalize_email("mailto:nowhere@mozilla.org?subject=Hello&body=World") == "nowhere@mozilla.org"
    assert normalize_email("nowhere@mozilla.org,nobody@mozilla.org") == "nowhere@mozilla.org"


def test_extract_contacts_skips_mozilla_documentation_examples() -> None:
    markdown = """
    Send email to nowhere <nowhere@mozilla.org>
    [nobody](mailto:nobody@mozilla.org)
    <a href="mailto:nowhere@mozilla.org?cc=nobody@mozilla.org">mailto with cc</a>
    Concatenated: nowhere@mozilla.org,nobody@mozilla.org
  """
    contacts = extract_contacts(markdown=markdown)
    assert contacts == []


def test_extract_contacts_keeps_real_email_on_same_page() -> None:
    markdown = """
    Docs: nowhere@mozilla.org
    Jane Doe <jane@realstudio.com>
    """
    contacts = extract_contacts(markdown=markdown)
    assert len(contacts) == 1
    assert contacts[0].email == "jane@realstudio.com"


def test_verifier_marks_dummy_local_invalid_despite_mx() -> None:
    result = check_email("nowhere@mozilla.org", resolver=_mozilla_mx_resolver())
    assert result.status == ContactVerificationStatus.INVALID
    assert any("dummy" in reason.lower() for reason in result.reasons)


@pytest.mark.parametrize(
    "email",
    [
        "info@example.com",
        "hello@helpy.io",
        "name@domain.com",
        "our.team@vocabulary.com",
    ],
)
def test_verifier_rejects_role_and_placeholder_before_mx(email: str) -> None:
    result = check_email(email, resolver=_mozilla_mx_resolver())
    assert result.status == ContactVerificationStatus.INVALID
    assert result.status != ContactVerificationStatus.VALID


def test_backfill_removes_dummy_documentation_profiles(db_url) -> None:
    upsert_contact_profile(
        email="jane@realstudio.com",
        name="Jane Doe",
        brand=Brand.TACTIC_STUDIO,
        source_url="https://realstudio.com/team",
    )

    with session_scope() as session:
        legacy_lead = Lead(
            email="nowhere@mozilla.org",
            name="Send email to nowhere",
            source=LeadSource.CONTACT,
            brand=Brand.TACTIC_STUDIO,
            status=LeadStatus.NEW,
            raw_payload={"found_on": ["https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/your_first_extension"]},
        )
        session.add(legacy_lead)
        session.flush()
        session.add(
            ContactProfile(
                email="nowhere@mozilla.org",
                name="Send email to nowhere",
                brand=Brand.TACTIC_STUDIO,
                email_kind=ContactEmailKind.JUNK,
                source_urls=[
                    "https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/your_first_extension"
                ],
                lead_id=legacy_lead.id,
            )
        )
        session.flush()

    result = backfill_contact_quality(limit=50, dry_run=False)
    assert result.profiles_removed == 1
    assert result.profiles_scanned == 2

    with session_scope() as session:
        rows = session.scalars(select(ContactProfile)).all()
        assert len(rows) == 1
        assert rows[0].email == "jane@realstudio.com"

        removed_lead = session.scalar(select(Lead).where(Lead.email == "nowhere@mozilla.org"))
        assert removed_lead is not None
        assert removed_lead.status == LeadStatus.DISQUALIFIED
