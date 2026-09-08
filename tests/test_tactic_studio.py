"""Tests for tactic.studio gov-grant hunt seeds, audiences, and contact labeling."""

from __future__ import annotations

import pytest

from agent_crm.contacts.store import process_scraped_page_contacts, upsert_contact_profile
from agent_crm.db import init_db, reset_engine, session_scope
from agent_crm.enums import Brand, ContactAudience
from agent_crm.hunt.seeds import seed_query_entries, seeds_for_brand
from agent_crm.models import ContactProfile, Lead
from sqlalchemy import select


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    db_path = tmp_path / "tactic_studio.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    from agent_crm.config import get_settings

    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield f"sqlite:///{db_path}"
    reset_engine()
    get_settings.cache_clear()


def test_tactic_studio_seeds_cover_grant_institution_terms() -> None:
    seeds = seeds_for_brand(Brand.TACTIC_STUDIO)
    combined = " ".join(seeds).lower()
    assert "imls" in combined or "neh" in combined or "nea" in combined
    assert "museum" in combined or "campus" in combined or "cultural" in combined
    assert "grant" in combined
    assert "interactive" in combined or "immersive" in combined or "exhibit" in combined

    entries = seed_query_entries(Brand.TACTIC_STUDIO)
    origins = {origin for _, origin in entries}
    assert "marketing:seed_pack" in origins
    assert "influencer:seed_pack" in origins
    assert "user:seed_pack" in origins

    marketing = " ".join(query for query, origin in entries if origin == "marketing:seed_pack")
    marketing_lower = marketing.lower()
    assert "museum" in marketing_lower or "university" in marketing_lower
    assert "grant" in marketing_lower
    assert "imls" in marketing_lower or "neh" in marketing_lower or "nea" in marketing_lower


def test_marketing_origin_labels_contact_profile(db_url) -> None:
    markdown = "Jane Marketing <jane@brandstudio.com>\nhttps://x.com/janemarketing"
    profiles = process_scraped_page_contacts(
        markdown=markdown,
        source_url="https://reddit.com/r/augmentedreality",
        brand=Brand.TACTIC_STUDIO,
        audience=ContactAudience.MARKETING,
    )
    assert len(profiles) == 1
    assert profiles[0].audience == ContactAudience.MARKETING

    with session_scope() as session:
        lead = session.scalar(select(Lead).where(Lead.email == "jane@brandstudio.com"))
        assert lead is not None
        assert lead.audience == ContactAudience.MARKETING


def test_list_contacts_by_audience(db_url) -> None:
    upsert_contact_profile(
        email="alex@brandstudio.com",
        name="Marketing Lead",
        brand=Brand.TACTIC_STUDIO,
        source_url="https://brandstudio.com/team",
        audience=ContactAudience.MARKETING,
    )
    upsert_contact_profile(
        email="creator@socialstudio.com",
        name="XR Creator",
        brand=Brand.TACTIC_STUDIO,
        source_url="https://youtube.com/c/xrreview",
        audience=ContactAudience.INFLUENCER,
    )

    from agent_crm.contacts.store import list_contact_profiles

    marketing_only = list_contact_profiles(
        brand=Brand.TACTIC_STUDIO,
        audience=ContactAudience.MARKETING,
    )
    assert len(marketing_only) == 1
    assert marketing_only[0].email == "alex@brandstudio.com"

    with session_scope() as session:
        rows = session.scalars(select(ContactProfile)).all()
        assert len(rows) == 2
