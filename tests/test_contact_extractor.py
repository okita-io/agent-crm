"""Tests for contact extraction, profiles, and social lookup."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.contacts.extractor import extract_contacts, is_skipped_email
from agent_crm.contacts.social_lookup import build_social_queries, lookup_social_profiles
from agent_crm.contacts.store import (
    ContactExtractionBudget,
    count_contact_profiles,
    count_contact_profiles_by_brand,
    list_contact_profiles,
    process_scraped_page_contacts,
    upsert_contact_profile,
)
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import Brand, ContactAudience, LeadSource
from agent_crm.models import ContactProfile, Lead
from agent_crm.db import session_scope
from sqlalchemy import select


FIXTURE_MARKDOWN = """# Team

Reach us on X: https://x.com/novastudio

Jane Doe <jane@novastudio.com>

General inquiries:
info@novastudio.com

Do not use noreply@novastudio.com or privacy@example.com.
"""

FIXTURE_HTML = """
<a href="mailto:support@novastudio.com">Support Team</a>
"""


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    db_path = tmp_path / "contacts.db"
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


def test_extract_contacts_finds_two_emails_and_social() -> None:
    contacts = extract_contacts(markdown=FIXTURE_MARKDOWN, html=FIXTURE_HTML)
    emails = {contact.email for contact in contacts}
    assert emails == {"jane@novastudio.com", "info@novastudio.com", "support@novastudio.com"}

    jane = next(c for c in contacts if c.email == "jane@novastudio.com")
    assert jane.name == "Jane Doe"
    assert jane.socials.get("x") == "https://x.com/novastudio"

    info = next(c for c in contacts if c.email == "info@novastudio.com")
    assert info.name is None

    support = next(c for c in contacts if c.email == "support@novastudio.com")
    assert support.name == "Support Team"


def test_extract_contacts_skips_blocked_addresses() -> None:
    markdown = """
    Contact: no-reply@acme.com
    Also: donotreply@acme.com
    Alerts: notifications@acme.com
    Demo: test@example.com
    Sentry: errors@sentry.io
    """
    contacts = extract_contacts(markdown=markdown)
    assert contacts == []
    assert is_skipped_email("noreply@company.com")
    assert is_skipped_email("privacy@company.com")
    assert is_skipped_email("mailer-daemon@company.com")


def test_upsert_contact_profile_merges_sources_and_socials(db_url) -> None:
    first = upsert_contact_profile(
        email="jane@novastudio.com",
        name="Jane Doe",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://novastudio.com/team",
        socials={"x": "https://x.com/novastudio"},
    )
    second = upsert_contact_profile(
        email="jane@novastudio.com",
        name=None,
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://novastudio.com/about",
        socials={"linkedin": "https://linkedin.com/in/janedoe"},
    )

    assert first.id == second.id
    assert second.source_urls == [
        "https://novastudio.com/team",
        "https://novastudio.com/about",
    ]
    assert second.socials == {
        "x": "https://x.com/novastudio",
        "linkedin": "https://linkedin.com/in/janedoe",
    }
    assert second.name == "Jane Doe"


def test_process_scraped_page_contacts_creates_profiles_and_leads(db_url) -> None:
    profiles = process_scraped_page_contacts(
        markdown=FIXTURE_MARKDOWN,
        html=FIXTURE_HTML,
        source_url="https://novastudio.com/team",
        brand=Brand.MIDNIGHTSATIN,
        budget=ContactExtractionBudget(
            social_lookups_remaining=0,
            enrichments_remaining=0,
            spark_enrichments_remaining=0,
        ),
    )
    # info@ and support@ are rejected as role/support inboxes at ingest
    assert len(profiles) == 1

    with session_scope() as session:
        rows = session.scalars(select(ContactProfile)).all()
        assert len(rows) == 1
        lead = session.scalar(select(Lead).where(Lead.email == "jane@novastudio.com"))
        assert lead is not None
        assert lead.source == LeadSource.CONTACT
        assert lead.name == "Jane Doe"


def test_social_lookup_mocked_searxng(db_url) -> None:
    searx_payload = {
        "results": [
            {
                "url": "https://x.com/janedoe",
                "title": "Jane Doe (@janedoe) / X",
                "content": "jane@novastudio.com",
            }
        ]
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=searx_payload)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    socials, queries_used = lookup_social_profiles(
        email="jane@novastudio.com",
        name="Jane Doe",
        client=http,
        max_queries=1,
    )
    assert queries_used == 1
    assert socials.get("x") == "https://x.com/janedoe"


def test_matches_contact_rejects_short_local_substring() -> None:
    from agent_crm.contacts.social_lookup import _matches_contact

    assert not _matches_contact(
        email="art@studio.com",
        name=None,
        url="https://linkedin.com/in/arthur-smith",
        title="Arthur Smith",
        snippet="XR designer at Arthur Labs",
    )
    assert not _matches_contact(
        email="ann@studio.com",
        name=None,
        url="https://x.com/anniversary",
        title="Anniversary Account",
        snippet="announcements",
    )
    assert not _matches_contact(
        email="mail@studio.com",
        name=None,
        url="https://linkedin.com/in/email-marketer",
        title="Email Marketer",
        snippet="mailing list tips",
    )


def test_matches_contact_accepts_word_boundary_local() -> None:
    from agent_crm.contacts.social_lookup import _matches_contact

    assert _matches_contact(
        email="janedoe@studio.com",
        name=None,
        url="https://x.com/other",
        title="Profile",
        snippet="Contact janedoe for collabs",
    )
    assert _matches_contact(
        email="jane.doe@studio.com",
        name="Jane Doe",
        url="https://linkedin.com/in/janedoe",
        title="Jane Doe",
        snippet="Designer",
    )


def test_build_social_queries_respects_cap(monkeypatch) -> None:
    monkeypatch.setenv("CRM_CONTACT_SOCIAL_QUERIES_PER_PROFILE", "2")
    from agent_crm.config import get_settings

    get_settings.cache_clear()
    queries = build_social_queries("jane@novastudio.com", "Jane Doe")
    assert len(queries) == 2
    get_settings.cache_clear()


def test_social_lookup_budget_skips_after_cap(db_url) -> None:
    with patch("agent_crm.contacts.store.lookup_social_profiles") as mock_lookup:
        mock_lookup.return_value = ({"x": "https://x.com/found"}, 1)
        budget = ContactExtractionBudget(
            social_lookups_remaining=1,
            enrichments_remaining=0,
            spark_enrichments_remaining=0,
        )
        process_scraped_page_contacts(
            markdown="alice@novastudio.com\nbob@novastudio.com",
            source_url="https://novastudio.com/team",
            brand=Brand.UNASSIGNED,
            budget=budget,
        )
        assert mock_lookup.call_count == 1
        assert budget.social_lookups_remaining == 0


def test_list_contacts_api(api_client, db_url) -> None:
    upsert_contact_profile(
        email="found@helpy.io",
        name="Found Person",
        brand=Brand.HEYBUDDY,
        source_url="https://helpy.io",
        socials={"x": "https://x.com/found"},
    )
    response = api_client.get("/contacts?brand=heybuddy")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["email"] == "found@helpy.io"
    assert payload[0]["socials"]["x"] == "https://x.com/found"
    assert response.headers["X-Total-Count"] == "1"


def test_contacts_summary_and_pagination(db_url) -> None:
    upsert_contact_profile(
        email="ms-one@novastudio.com",
        name="MS One",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://novastudio.com/ms-one",
    )
    upsert_contact_profile(
        email="ts-one@tactic.studio",
        name="TS One",
        brand=Brand.TACTIC_STUDIO,
        source_url="https://tactic.studio/ts-one",
        audience=ContactAudience.MARKETING,
    )
    upsert_contact_profile(
        email="ts-two@tactic.studio",
        name="TS Two",
        brand=Brand.TACTIC_STUDIO,
        source_url="https://tactic.studio/ts-two",
        audience=ContactAudience.MARKETING,
    )

    assert count_contact_profiles() == 3
    by_brand = {row["brand"]: row["count"] for row in count_contact_profiles_by_brand()}
    assert by_brand["midnightsatin"] == 1
    assert by_brand["tactic-studio"] == 2

    page = list_contact_profiles(limit=1, offset=0)
    assert len(page) == 1
    assert count_contact_profiles(brand=Brand.TACTIC_STUDIO, audience=ContactAudience.MARKETING) == 2


def test_contacts_summary_api(api_client, db_url) -> None:
    upsert_contact_profile(
        email="alpha@novastudio.com",
        name=None,
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://novastudio.com/alpha",
    )
    upsert_contact_profile(
        email="beta@tactic.studio",
        name=None,
        brand=Brand.TACTIC_STUDIO,
        source_url="https://tactic.studio/beta",
        audience=ContactAudience.MARKETING,
    )

    response = api_client.get("/contacts/summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    by_brand = {row["brand"]: row["count"] for row in payload["by_brand"]}
    assert by_brand["midnightsatin"] == 1
    assert by_brand["tactic-studio"] == 1

    filtered = api_client.get("/contacts/summary?audience=marketing")
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1


def test_list_contacts_api_offset_and_total_header(api_client, db_url) -> None:
    for index in range(3):
        upsert_contact_profile(
            email=f"user{index}@helpy.io",
            name=None,
            brand=Brand.HEYBUDDY,
            source_url=f"https://helpy.io/{index}",
        )

    response = api_client.get("/contacts?brand=heybuddy&limit=1&offset=1")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.headers["X-Total-Count"] == "3"


def test_process_scraped_page_does_not_invent_names(db_url) -> None:
    markdown = "Email us at jane@studio.io\nhttps://x.com/studio"
    profiles = process_scraped_page_contacts(
        markdown=markdown,
        source_url="https://studio.io",
        brand=Brand.UNASSIGNED,
        budget=ContactExtractionBudget(
            social_lookups_remaining=0,
            enrichments_remaining=0,
            spark_enrichments_remaining=0,
        ),
    )
    assert len(profiles) == 1
    assert profiles[0].name is None
    assert profiles[0].socials == {"x": "https://x.com/studio"}
