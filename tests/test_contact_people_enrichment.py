"""Tests for public people-enrichment on contact profiles."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.contact_people_enrichment import (
    collect_page_evidence,
    enrich_contact_person,
    is_login_walled_url,
    parse_linkedin_serp_title,
    SerpEvidence,
)
from agent_crm.contact_quality import is_role_inbox_email
from agent_crm.contact_store import (
    ContactExtractionBudget,
    backfill_contact_enrichment,
    process_scraped_page_contacts,
    upsert_contact_profile,
    _persist_enrichment,
)
from agent_crm.contact_people_enrichment import PeopleEnrichmentFields, PeopleEnrichmentResult
from agent_crm.db import init_db, reset_engine, session_scope
from agent_crm.enums import Brand
from agent_crm.models import ContactProfile
from sqlalchemy import select


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    db_path = tmp_path / "enrich.db"
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


def test_parse_linkedin_serp_title_vp_at_org() -> None:
    fields = parse_linkedin_serp_title("Jane Doe - VP Marketing at Acme | LinkedIn")
    assert fields.name == "Jane Doe"
    assert fields.title == "VP Marketing"
    assert fields.organization == "Acme"


def test_parse_linkedin_serp_title_name_only() -> None:
    fields = parse_linkedin_serp_title("Jane Doe | LinkedIn")
    assert fields.name == "Jane Doe"
    assert fields.title is None


def test_is_login_walled_url_blocks_social_hosts() -> None:
    assert is_login_walled_url("https://www.linkedin.com/in/jane")
    assert is_login_walled_url("https://facebook.com/jane")
    assert is_login_walled_url("https://x.com/jane")
    assert not is_login_walled_url("https://acme.com/team")


def test_collect_page_evidence_skips_login_walled_scrape() -> None:
    with patch("agent_crm.contact_people_enrichment.scrape") as mock_scrape:
        evidence, _ = collect_page_evidence(
            email="jane@acme.com",
            name="Jane Doe",
            serp_evidence=[
                SerpEvidence(
                    url="https://linkedin.com/in/janedoe",
                    title="Jane | LinkedIn",
                    snippet="",
                    platform="linkedin",
                ),
                SerpEvidence(
                    url="https://acme.com/press/jane",
                    title="Jane Doe press",
                    snippet="Jane Doe VP",
                    platform=None,
                ),
            ],
            max_pages=2,
        )
        called_urls = [call.args[0] for call in mock_scrape.call_args_list]
        assert "https://linkedin.com/in/janedoe" not in called_urls
        assert any("acme.com" in url for url in called_urls)
        assert len(evidence) <= 2


def test_is_role_inbox_email_skips_generic_addresses() -> None:
    assert is_role_inbox_email("info@acme.com")
    assert is_role_inbox_email("hello@acme.com")
    assert is_role_inbox_email("contact@acme.com")
    assert is_role_inbox_email("support@acme.com")
    assert not is_role_inbox_email("jane.doe@acme.com")


def test_enrich_contact_person_skips_role_inbox() -> None:
    assert enrich_contact_person(email="info@acme.com", name=None) is None


def test_persist_enrichment_fills_name_title_org(db_url) -> None:
    upsert_contact_profile(
        email="jane@acme.com",
        name=None,
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://acme.com/team",
    )
    result = PeopleEnrichmentResult(
        fields=PeopleEnrichmentFields(
            name="Jane Doe",
            title="VP Marketing",
            organization="Acme Corp",
            socials={"linkedin": "https://linkedin.com/in/janedoe"},
        ),
        queries_used=2,
    )
    profile = _persist_enrichment(email="jane@acme.com", result=result)
    assert profile.name == "Jane Doe"
    assert profile.title == "VP Marketing"
    assert profile.organization == "Acme Corp"
    assert profile.socials["linkedin"] == "https://linkedin.com/in/janedoe"
    assert profile.enrichment is not None
    assert profile.enrichment["queries_used"] == 2


def test_contacts_api_round_trip_enrichment_fields(api_client, db_url) -> None:
    upsert_contact_profile(
        email="jane@acme.com",
        name="Jane Doe",
        brand=Brand.HEYBUDDY,
        source_url="https://acme.com",
    )
    result = PeopleEnrichmentResult(
        fields=PeopleEnrichmentFields(
            title="Director",
            organization="Acme",
            location="Austin, TX",
            bio="Marketing leader.",
        ),
    )
    _persist_enrichment(email="jane@acme.com", result=result)

    response = api_client.get("/contacts?email=jane@acme.com")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    row = payload[0]
    assert row["title"] == "Director"
    assert row["organization"] == "Acme"
    assert row["location"] == "Austin, TX"
    assert row["bio"] == "Marketing leader."
    assert row["enrichment"] is not None


def test_process_scraped_page_enrichment_mocked(db_url) -> None:
    searx_payload = {
        "results": [
            {
                "url": "https://linkedin.com/in/janedoe",
                "title": "Jane Doe - VP Marketing at Acme | LinkedIn",
                "content": "jane@acme.com",
            }
        ]
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=searx_payload)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    budget = ContactExtractionBudget(
        social_lookups_remaining=0,
        enrichments_remaining=5,
        spark_enrichments_remaining=0,
    )
    profiles = process_scraped_page_contacts(
        markdown="Jane Doe <jane@acme.com>",
        source_url="https://acme.com/team",
        brand=Brand.MIDNIGHTSATIN,
        searx_client=http,
        budget=budget,
    )
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.title == "VP Marketing"
    assert profile.organization == "Acme"
    assert profile.name == "Jane Doe"


def test_backfill_contact_enrichment_cli_shape(db_url) -> None:
    upsert_contact_profile(
        email="found@example.com",
        name="Found Person",
        brand=Brand.UNASSIGNED,
        source_url="https://example.com",
    )

    with patch("agent_crm.contact_store.enrich_contact_person") as mock_enrich:
        mock_enrich.return_value = PeopleEnrichmentResult(
            fields=PeopleEnrichmentFields(title="CEO", organization="Example Inc"),
            spark_used=True,
        )
        result = backfill_contact_enrichment(limit=10, dry_run=True)
        assert result.profiles_scanned == 1
        assert result.profiles_enriched == 1
        assert result.spark_calls == 1


def test_contacts_enrich_api(api_client, db_url) -> None:
    upsert_contact_profile(
        email="api@example.com",
        name="API Person",
        brand=Brand.UNASSIGNED,
        source_url="https://example.com",
    )
    with patch("agent_crm.contact_store.enrich_contact_person") as mock_enrich:
        mock_enrich.return_value = PeopleEnrichmentResult(
            fields=PeopleEnrichmentFields(organization="Example Co"),
        )
        response = api_client.post(
            "/contacts/enrich",
            json={"limit": 5, "dry_run": True},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["profiles_scanned"] == 1
        assert payload["profiles_enriched"] == 1
