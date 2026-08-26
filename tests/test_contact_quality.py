"""Tests for contact-quality filters and backfill."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.contact_extractor import extract_contacts
from agent_crm.contact_quality import (
    clean_contact_data,
    filter_socials,
    is_generic_support_email,
    is_relevant_contact,
    is_relevant_source_url,
    is_share_link_social_url,
    scrub_notes_value,
    scrub_tracking_pixel_urls,
)
from agent_crm.contact_store import backfill_contact_quality, upsert_contact_profile
from agent_crm.db import init_db, reset_engine, session_scope
from agent_crm.enums import Brand, HuntResourceKind, LeadSource, LeadStatus
from agent_crm.models import ContactProfile, HuntResource, Lead
from sqlalchemy import select


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    db_path = tmp_path / "contact_quality.db"
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


def test_relevant_source_url_accepts_matching_domain() -> None:
    assert is_relevant_source_url("https://novastudio.com/team", "jane@novastudio.com")
    assert is_relevant_contact(
        "jane@novastudio.com",
        ["https://novastudio.com/team"],
    )


def test_relevant_source_url_accepts_community_pages() -> None:
    assert is_relevant_source_url(
        "https://reddit.com/r/writing",
        "author@other-domain.com",
    )
    assert is_relevant_contact(
        "author@other-domain.com",
        ["https://reddit.com/r/writing"],
    )


def test_irrelevant_source_url_rejects_tracking_domains() -> None:
    assert not is_relevant_source_url(
        "https://pixel.ads.example.net/open.gif",
        "jane@novastudio.com",
    )
    assert not is_relevant_contact(
        "jane@novastudio.com",
        ["https://pixel.ads.example.net/open.gif"],
    )


def test_irrelevant_source_url_rejects_legal_only_pages() -> None:
    assert not is_relevant_source_url(
        "https://unrelated.example/privacy-policy",
        "jane@novastudio.com",
    )


def test_generic_support_email_filtered() -> None:
    assert is_generic_support_email("support@agency.com")
    assert is_generic_support_email("helpdesk@brand.io")
    assert not is_generic_support_email("jane.doe@studio.com")


def test_share_link_socials_are_stripped() -> None:
    socials = {
        "x": "https://x.com/intent/tweet?text=hello",
        "facebook": "https://facebook.com/sharer.php?u=https://example.com",
        "linkedin": "https://linkedin.com/in/janedoe",
    }
    cleaned = filter_socials(socials, email="jane@example.com")
    assert cleaned == {"linkedin": "https://linkedin.com/in/janedoe"}


def test_generic_support_social_handles_are_stripped() -> None:
    socials = {
        "x": "https://x.com/support",
        "instagram": "https://instagram.com/help",
        "facebook": "https://facebook.com/janedoe",
    }
    cleaned = filter_socials(socials, email="jane@example.com")
    assert cleaned == {"facebook": "https://facebook.com/janedoe"}


def test_ad_firm_social_urls_are_stripped() -> None:
    socials = {
        "linkedin": "https://linkedin.com/company/ogilvy",
        "x": "https://x.com/janedoe",
    }
    cleaned = filter_socials(socials, email="jane@example.com")
    assert cleaned == {"x": "https://x.com/janedoe"}


def test_scrub_tracking_pixel_urls_from_notes() -> None:
    text = (
        "Great thread. Opened https://track.mail.example/open.gif?uid=1 "
        "and https://doubleclick.net/pixel?id=2 for stats."
    )
    cleaned = scrub_tracking_pixel_urls(text)
    assert "track.mail.example" not in cleaned
    assert "doubleclick.net" not in cleaned
    assert cleaned.startswith("Great thread.")


def test_scrub_tracking_pixels_from_json_notes() -> None:
    notes = json.dumps(
        {
            "community": "reddit",
            "slug": "writing",
            "snippet": "Seen via https://beacon.example.net/pixel.gif",
        }
    )
    cleaned = scrub_notes_value(notes)
    payload = json.loads(cleaned or "")
    assert "beacon.example.net" not in payload["snippet"]


def test_extract_contacts_strips_share_link_socials() -> None:
    markdown = """
    Jane Doe <jane@studio.com>
    Share: https://x.com/intent/tweet?url=https://studio.com
    Profile: https://x.com/janestudio
    """
    contacts = extract_contacts(markdown=markdown)
    assert len(contacts) == 1
    assert contacts[0].socials.get("x") == "https://x.com/janestudio"


def test_backfill_cleans_existing_profile_without_inventing_contacts(db_url) -> None:
    upsert_contact_profile(
        email="jane@studio.com",
        name="Jane Doe",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://studio.com/team",
        socials={
            "x": "https://x.com/support",
            "linkedin": "https://linkedin.com/in/janedoe",
        },
    )
    upsert_contact_profile(
        email="help@agency.com",
        name=None,
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://pixel.ads.example.net/open.gif",
        socials={"x": "https://x.com/help"},
    )

    with session_scope() as session:
        session.add(
            HuntResource(
                url="https://reddit.com/r/test",
                domain="reddit.com",
                brand=Brand.MIDNIGHTSATIN,
                kind=HuntResourceKind.COMMUNITY,
                notes=json.dumps(
                    {
                        "community": "reddit",
                        "snippet": "Tracked https://track.mail.example/open.gif",
                    }
                ),
            )
        )

    result = backfill_contact_quality(limit=50, dry_run=False)
    assert result.profiles_scanned == 2
    assert result.profiles_removed == 1
    assert result.profiles_updated == 1
    assert result.resource_notes_scrubbed == 1

    with session_scope() as session:
        rows = session.scalars(select(ContactProfile)).all()
        assert len(rows) == 1
        assert rows[0].email == "jane@studio.com"
        assert rows[0].socials == {"linkedin": "https://linkedin.com/in/janedoe"}

        removed_lead = session.scalar(select(Lead).where(Lead.email == "help@agency.com"))
        assert removed_lead is not None
        assert removed_lead.status == LeadStatus.DISQUALIFIED

        resource = session.scalar(select(HuntResource))
        assert resource is not None
        assert "track.mail.example" not in (resource.notes or "")


def test_backfill_dry_run_reports_without_writing(db_url) -> None:
    upsert_contact_profile(
        email="support@studio.com",
        name=None,
        brand=Brand.HEYBUDDY,
        source_url="https://studio.com/contact",
        socials={"x": "https://x.com/support"},
    )

    result = backfill_contact_quality(limit=10, dry_run=True)
    assert result.profiles_removed == 1
    assert result.profiles_updated == 0

    with session_scope() as session:
        assert session.scalar(select(ContactProfile)) is not None


def test_contacts_backfill_api(api_client, db_url) -> None:
    upsert_contact_profile(
        email="jane@studio.com",
        name="Jane Doe",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://studio.com/about",
        socials={"x": "https://x.com/intent/tweet"},
    )
    response = api_client.post("/contacts/backfill", json={"limit": 10, "dry_run": False})
    assert response.status_code == 200
    payload = response.json()
    assert payload["profiles_scanned"] == 1
    assert payload["profiles_updated"] == 1


def test_clean_contact_data_keeps_relevant_profile() -> None:
    socials, urls, keep, cleanup = clean_contact_data(
        email="jane@studio.com",
        socials={"x": "https://x.com/janestudio"},
        source_urls=["https://studio.com/team"],
    )
    assert keep is True
    assert urls == ["https://studio.com/team"]
    assert socials == {"x": "https://x.com/janestudio"}
    assert cleanup.removed_source_urls == []
