"""Tests for research follow-up term extraction."""

from __future__ import annotations

from agent_crm.enums import Brand, ResearchFindingKind
from agent_crm.research.feedback import extract_research_follow_up_terms


def test_extract_follow_ups_from_serp_and_page_for_each_brand() -> None:
    cases = [
        (
            Brand.CELESTIAL_NEXUS,
            ResearchFindingKind.COMPETITOR,
            "natal chart app",
            [{"title": "Rune Casting App", "content": "pendulum and I Ching"}],
            ["Palmistry and scrying lessons with Lenormand decks."],
            ("rune", "pendulum", "i ching", "palmistry", "scrying", "lenormand"),
        ),
        (
            Brand.MIDNIGHTSATIN,
            ResearchFindingKind.COMPETITOR,
            "serialized romance app",
            [{"title": "Galatea romance app", "content": "BookTok spicy romance"}],
            ["Radish and Dreame compete with Galatea on romantasy."],
            ("galatea", "radish", "dreame", "booktok"),
        ),
        (
            Brand.HEYBUDDY,
            ResearchFindingKind.NONPROFIT,
            "501c3 loneliness nonprofit",
            [{"title": "Veterans peer support 501c3", "content": "elder isolation"}],
            ["Caregiver support and youth digital wellbeing programs."],
            ("veteran", "caregiver", "elder isolation", "youth digital wellbeing"),
        ),
        (
            Brand.TACTIC_STUDIO,
            ResearchFindingKind.COMPETITOR,
            "WebAR studio",
            [{"title": "Industrial visualization AR experience studio", "content": ""}],
            ["Industrial training aids and digital twin CAD visualization."],
            ("industrial visualization", "training aid", "digital twin"),
        ),
        (
            Brand.TACTIC_STUDIO,
            ResearchFindingKind.TARGET_COMPANY,
            "largest US grocery chains",
            [{"title": "Top grocery supermarket chains", "content": "Kroger grocery"}],
            ["Regional grocery chains and convenience store retailers."],
            ("grocery", "convenience store"),
        ),
    ]
    for brand, kind, query, serp, pages, expected in cases:
        terms = extract_research_follow_up_terms(
            query=query,
            brand=brand,
            kind=kind,
            serp_results=serp,
            page_texts=pages,
            max_terms=8,
        )
        combined = " ".join(terms).lower()
        assert terms, f"expected follow-ups for {brand.value}"
        assert any(token in combined for token in expected), combined
        assert query.lower() not in {term.lower() for term in terms}
