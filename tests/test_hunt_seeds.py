"""Tests for per-brand hunt seed packs."""

from __future__ import annotations

from agent_crm.enums import Brand
from agent_crm.hunt_seeds import seeds_for_brand


def test_midnightsatin_seed_pack_includes_ai_generated_and_influencer_terms() -> None:
    seeds = seeds_for_brand(Brand.MIDNIGHTSATIN)
    combined = " ".join(seeds).lower()
    assert "ai generated" in combined
    assert "influencer" in combined
