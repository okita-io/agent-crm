"""Tests for per-brand hunt seed packs."""

from __future__ import annotations

from agent_crm.enums import Brand
from agent_crm.hunt.seeds import hunt_loop_brands, loop_seed_entries, seeds_for_brand


def test_midnightsatin_seed_pack_includes_ai_generated_and_influencer_terms() -> None:
    seeds = seeds_for_brand(Brand.MIDNIGHTSATIN)
    combined = " ".join(seeds).lower()
    assert "ai generated" in combined
    assert "influencer" in combined
    assert "high traffic" in combined or "high engagement" in combined


def test_tactic_studio_seed_pack_non_empty() -> None:
    seeds = seeds_for_brand(Brand.TACTIC_STUDIO)
    assert len(seeds) >= 20


def test_loop_seed_entries_cover_armed_hunt_brands() -> None:
    entries = loop_seed_entries()
    brands = {brand for brand, _query, _origin in entries}
    expected = set(hunt_loop_brands())
    assert brands == expected
    base_count = sum(len(seeds_for_brand(brand)) for brand in expected)
    assert len(entries) >= base_count
