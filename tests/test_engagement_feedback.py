"""Tests for engagement follow-up term extraction."""

from __future__ import annotations

from agent_crm.engagement_feedback import extract_engagement_follow_up_terms
from agent_crm.enums import Brand


def test_extract_engagement_follow_ups_finds_related_subs_and_hints() -> None:
    terms = extract_engagement_follow_up_terms(
        query="site:reddit.com/r/RomanceBooks hot",
        brand=Brand.MIDNIGHTSATIN,
        serp_results=[
            {
                "title": "Weekly recs megathread",
                "url": "https://www.reddit.com/r/RomanceBooks/comments/abc/weekly/",
                "content": "See also r/HistoricalRomance and Galatea spicy romance.",
            }
        ],
        page_texts=["Radish readers and r/Romance_for_men cross-post here."],
        max_terms=8,
    )
    combined = " ".join(terms).lower()
    assert terms
    assert "historicalromance" in combined or "romance_for_men" in combined
    assert "galatea" in combined or "radish" in combined
    assert "site:reddit.com/r/romancebooks hot" not in {t.lower() for t in terms}
