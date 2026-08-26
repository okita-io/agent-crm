"""Tests for person vs role email quality filters and obfuscation decode."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.contact_extractor import (
    decode_obfuscated_email_deterministic,
    decode_obfuscated_emails_spark,
    extract_contacts,
)
from agent_crm.contact_store import ContactExtractionBudget
from agent_crm.contact_quality import (
    is_filename_as_email,
    is_person_email,
    is_placeholder_email,
    is_role_inbox_email,
    local_part_has_person_signals,
)
from agent_crm.contact_store import (
    count_contact_profiles,
    count_contact_profiles_by_quality,
    list_contact_profiles,
    upsert_contact_profile,
)
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import Brand, ContactAudience


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    db_path = tmp_path / "person_quality.db"
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


@pytest.mark.parametrize(
    ("text", "expected_email"),
    [
        ("jane at acme dot com", "jane@acme.com"),
        ("jane[at]acme[dot]com", "jane@acme.com"),
        ("jane (at) acme (dot) com", "jane@acme.com"),
        ("jane@acme dot com", "jane@acme.com"),
        ("jane doe at acme.com", "jane.doe@acme.com"),
        ("first last dot company dot com", "first.last@company.com"),
        ("reach me at jane&#64;acme&#46;com", "jane@acme.com"),
        ("email: jane%40acme.com", "jane@acme.com"),
    ],
)
def test_decode_obfuscated_email_deterministic(text: str, expected_email: str) -> None:
    decoded = decode_obfuscated_email_deterministic(text)
    emails = {email for email, _ in decoded}
    assert expected_email in emails


def test_extract_contacts_decodes_spaced_dot_pattern() -> None:
    contacts = extract_contacts(
        markdown="Reach us: first last dot company dot com",
        budget=ContactExtractionBudget(social_lookups_remaining=0, spark_decode_remaining=0),
    )
    assert len(contacts) == 1
    assert contacts[0].email == "first.last@company.com"
    assert contacts[0].decoded_from_obfuscation


def test_spark_decode_stubbed_no_live_spark() -> None:
    budget = ContactExtractionBudget(
        social_lookups_remaining=0,
        spark_decode_remaining=1,
    )
    with patch("agent_crm.llm_client.chat_completions") as mock_chat:
        mock_chat.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"emails":[{"email":"spark@decoded.io","name":"Spark User"}]}',
                    }
                }
            ]
        }
        found = decode_obfuscated_emails_spark(
            "obfuscated spark at decoded dot io",
            budget=budget,
        )
    assert found == [("spark@decoded.io", "Spark User")]
    assert budget.spark_decode_remaining == 0
    mock_chat.assert_called_once()


def test_spark_decode_skipped_when_budget_zero() -> None:
    budget = ContactExtractionBudget(social_lookups_remaining=0, spark_decode_remaining=0)
    with patch("agent_crm.llm_client.chat_completions") as mock_chat:
        assert decode_obfuscated_emails_spark("jane at acme dot com", budget=budget) == []
        mock_chat.assert_not_called()


def test_role_vs_person_classification() -> None:
    assert is_role_inbox_email("info@agency.com")
    assert is_role_inbox_email("hello@brand.io")
    assert is_role_inbox_email("support@studio.com")
    assert not is_role_inbox_email("jane.doe@studio.com")

    assert is_filename_as_email("logo@cdn.png")
    assert is_placeholder_email("name@domain.com")

    assert local_part_has_person_signals("jane.doe@studio.com")
    assert is_person_email("jane.doe@studio.com")
    assert is_person_email("jane@studio.com", name="Jane Doe")
    assert not is_person_email("info@studio.com")
    assert not is_person_email("logo@cdn.png")
    assert not is_person_email("name@domain.com")
    assert is_person_email("decoded@studio.com", decoded_from_obfuscation=True)


def test_contact_store_quality_filters(db_url) -> None:
    upsert_contact_profile(
        email="jane.doe@studio.com",
        name="Jane Doe",
        brand=Brand.TACTIC_STUDIO,
        source_url="https://studio.com/team",
    )
    upsert_contact_profile(
        email="info@studio.com",
        name=None,
        brand=Brand.TACTIC_STUDIO,
        source_url="https://studio.com/contact",
    )
    upsert_contact_profile(
        email="name@domain.com",
        name=None,
        brand=Brand.TACTIC_STUDIO,
        source_url="https://studio.com/placeholder",
    )

    counts = count_contact_profiles_by_quality(brand=Brand.TACTIC_STUDIO)
    assert counts["person"] == 1
    assert counts["role"] == 1
    assert counts["total"] == 3

    person_rows = list_contact_profiles(brand=Brand.TACTIC_STUDIO, quality="person")
    assert [row.email for row in person_rows] == ["jane.doe@studio.com"]
    assert count_contact_profiles(brand=Brand.TACTIC_STUDIO, quality="person") == 1


def test_contacts_api_person_filter(api_client, db_url) -> None:
    upsert_contact_profile(
        email="pete.smith@tactic.studio",
        name="Pete Smith",
        brand=Brand.TACTIC_STUDIO,
        source_url="https://tactic.studio",
        audience=ContactAudience.MARKETING,
    )
    upsert_contact_profile(
        email="hello@tactic.studio",
        name=None,
        brand=Brand.TACTIC_STUDIO,
        source_url="https://tactic.studio/contact",
    )

    response = api_client.get("/contacts?brand=tactic-studio&quality=person")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["email"] == "pete.smith@tactic.studio"
    assert response.headers["X-Total-Count"] == "1"

    person_only = api_client.get("/contacts?person_only=true&brand=tactic-studio")
    assert person_only.status_code == 200
    assert len(person_only.json()) == 1
