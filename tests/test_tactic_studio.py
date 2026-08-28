"""Tests for tactic.studio hunt seeds, audiences, and contact labeling."""

from __future__ import annotations

import pytest

from agent_crm.contact_store import process_scraped_page_contacts, upsert_contact_profile
from agent_crm.db import init_db, reset_engine, session_scope
from agent_crm.enums import Brand, ContactAudience
from agent_crm.hunt_seeds import seed_query_entries, seeds_for_brand
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


def test_tactic_studio_seeds_cover_audiences_and_xr_terms() -> None:
    seeds = seeds_for_brand(Brand.TACTIC_STUDIO)
    combined = " ".join(seeds).lower()
    assert "webar" in combined or "webxr" in combined
    assert "ar" in combined or "xr" in combined or "vr" in combined
    assert "influencer" in combined or "creator" in combined or "youtuber" in combined
    assert "reddit" in combined or "discord" in combined or "community" in combined

    entries = seed_query_entries(Brand.TACTIC_STUDIO)
    origins = {origin for _, origin in entries}
    assert "marketing:seed_pack" in origins
    assert "influencer:seed_pack" in origins
    assert "user:seed_pack" in origins

    marketing = " ".join(query for query, origin in entries if origin == "marketing:seed_pack")
    marketing_lower = marketing.lower()
    assert "vp" in marketing_lower or "vice president" in marketing_lower
    assert "marketing" in marketing_lower
    assert "brand" in marketing_lower
    assert "retail" in marketing_lower
    assert "food" in marketing_lower or "beverage" in marketing_lower
    assert "10 million" in marketing_lower or "$10" in marketing_lower


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

    from agent_crm.contact_store import list_contact_profiles

    marketing_only = list_contact_profiles(
        brand=Brand.TACTIC_STUDIO,
        audience=ContactAudience.MARKETING,
    )
    assert len(marketing_only) == 1
    assert marketing_only[0].email == "alex@brandstudio.com"

    with session_scope() as session:
        rows = session.scalars(select(ContactProfile)).all()
        assert len(rows) == 2
