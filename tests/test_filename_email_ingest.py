"""Tests for filename/retina asset emails that must never become CONTACT leads."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from agent_crm.contacts.extractor import (
    decode_obfuscated_email_deterministic,
    extract_contacts,
    is_skipped_email,
)
from agent_crm.contacts.quality import is_filename_as_email, prepare_contact_for_ingest
from agent_crm.contacts.store import process_scraped_page_contacts, upsert_contact_profile
from agent_crm.db import init_db, reset_engine, session_scope
from agent_crm.enums import Brand
from agent_crm.models import ContactProfile, Lead

pytestmark = pytest.mark.usefixtures("db_url")

FILENAME_AS_EMAIL_CASES = [
    "250x200@2x.png",
    "banner@2x.png",
    "logo-public-storage@3x.png",
    "education-image@2x.jpg",
    "img@1x.f29ac9dc.png",
    "cleanshot-2026-08-27-at-07.55.58@2x.png",
    "screenshot-2026-08-17-@-2.45.49-pm.png",
    "trucks-@-a-truck-stop-near-denver.jpg",
    "untitled-september-09-2025-@-09.50.28-8.jpeg",
    "whatsapp-image-2026-08-16-@-18.58.26-1.jpeg",
]


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    db_path = tmp_path / "filename_email.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    from agent_crm.config import get_settings

    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield f"sqlite:///{db_path}"
    reset_engine()
    get_settings.cache_clear()


@pytest.mark.parametrize("email", FILENAME_AS_EMAIL_CASES)
def test_is_filename_as_email_rejects_production_junk(email: str) -> None:
    assert is_filename_as_email(email)
    assert is_skipped_email(email)
    assert prepare_contact_for_ingest(email, None) is None


REAL_ZIP_TLD_EMAIL_CASES = [
    "someone@lemmy.zip",
    "someone@piefed.zip",
    "jane.doe@lemmy.zip",
    "alex@piefed.zip",
]


@pytest.mark.parametrize("email", REAL_ZIP_TLD_EMAIL_CASES)
def test_is_filename_as_email_allows_real_zip_tld_domains(email: str) -> None:
    """`.zip` is a public gTLD — federated social domains must not be flagged."""
    assert not is_filename_as_email(email)
    assert not is_skipped_email(email)


def test_prepare_contact_ingest_allows_real_zip_tld_person_email() -> None:
    assert prepare_contact_for_ingest("jane.doe@lemmy.zip", "Jane Doe") == (
        "jane.doe@lemmy.zip",
        "Jane Doe",
    )


@pytest.mark.parametrize("email", FILENAME_AS_EMAIL_CASES)
def test_upsert_rejects_filename_emails(email: str) -> None:
    with pytest.raises(ValueError, match="rejected at ingest"):
        upsert_contact_profile(
            email=email,
            name=None,
            brand=Brand.TACTIC_STUDIO,
            source_url="https://studio.com/contact",
        )


def test_replace_at_dot_tokens_does_not_decode_screenshot_filenames() -> None:
    markdown = """
    ![Screenshot 2026-08-17 at 2.45.49 PM.png](https://cdn.example/screenshot.png)
    CleanShot 2026-08-27 at 07.55.58@2x.png
    WhatsApp Image 2026-08-16 at 18.58.26.jpeg
    Patrol at La Guardia airport during rush hour.
    Contact Jane Doe <jane.doe@realstudio.com>
    """
    decoded = decode_obfuscated_email_deterministic(markdown)
    assert [email for email, _name in decoded] == ["jane.doe@realstudio.com"]

    contacts = extract_contacts(markdown=markdown)
    assert [contact.email for contact in contacts] == ["jane.doe@realstudio.com"]


def test_extract_contacts_skips_retina_asset_emails_in_html() -> None:
    html = """
    <img src="/assets/250x200@2x.png" alt="banner">
    <img src="/assets/logo-public-storage@3x.png" alt="logo">
    <a href="mailto:jane@realstudio.com">Jane</a>
    """
    contacts = extract_contacts(html=html)
    assert [contact.email for contact in contacts] == ["jane@realstudio.com"]


def test_process_scraped_page_contacts_does_not_store_filename_emails() -> None:
    markdown = """
    Assets: banner@2x.png education-image@2x.jpg img@1x.f29ac9dc.png
    Screenshot 2026-08-17 at 2.45.49 PM.png
    Reach jane.doe@realstudio.com for partnerships.
    """
    profiles = process_scraped_page_contacts(
        markdown=markdown,
        source_url="https://realstudio.com/contact",
        brand=Brand.TACTIC_STUDIO,
    )
    assert len(profiles) == 1
    assert profiles[0].email == "jane.doe@realstudio.com"

    with session_scope() as session:
        assert len(list(session.scalars(select(ContactProfile)))) == 1
        assert len(list(session.scalars(select(Lead)))) == 1
