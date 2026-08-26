"""Seed query packs for the Research agent."""

from __future__ import annotations

from .enums import Brand, ResearchFindingKind

COMPETITOR_QUERIES: dict[Brand, list[str]] = {
    Brand.CELESTIAL_NEXUS: [
        "natal chart app astrology reading",
        "birth chart calculator mobile app",
        "daily horoscope app subscription",
        "tarot reading app iOS Android",
        "astrology compatibility app",
        "divination reading app spiritual",
        "synastry chart app couples astrology",
        "moon phase astrology app",
        "vedic astrology app western",
        "psychic reading app online",
        "astrology SaaS platform API",
        "cosmic guidance app wellness astrology",
        "ai generated astrology content app",
        "AI tarot influencer platforms",
    ],
    Brand.MIDNIGHTSATIN: [
        "romance reading app serial fiction",
        "Kindle Unlimited romance app alternative",
        "serialized romance novel app",
        "book club app romance readers",
        "interactive romance story app",
        "Radish Fiction alternative romance",
        "Dreame romance reading app",
        "Galatea immersive romance app",
        "Wattpad romance premium app",
        "romance audiobook serial app",
        "booktok romance reading platform",
        "spicy romance serial app subscription",
        "ai generated romance fiction app",
        "AI-authored romance readers",
        "booktok ai generated stories",
        "influencers promoting AI novels",
    ],
    Brand.TACTIC_STUDIO: [
        "WebAR XR experience studio portfolio",
        "industrial AR training vendor enterprise",
        "8th Wall migration WebAR agency alternative",
        "immersive brand activation AR studio",
        "enterprise AR product visualization studio",
        "Snap AR lens creative production studio",
        "WebXR agency retail CPG campaigns",
        "augmented reality industrial training company",
        "mixed reality studio commercial activations",
        "AR glasses enterprise deployment vendor",
    ],
}

NONPROFIT_QUERIES: dict[Brand, list[str]] = {
    Brand.HEYBUDDY: [
        "501c3 loneliness elder companionship nonprofit",
        "501(c)(3) mental wellness veterans nonprofit",
        "nonprofit AI companion loneliness seniors",
        "501c3 youth digital wellbeing nonprofit",
        "veterans mental health nonprofit partnership programs",
        "elder isolation nonprofit United States",
        "501c3 social connection nonprofit grant",
        "mental health companion nonprofit technology partnership",
        "nonprofit loneliness epidemic community programs",
        "501c3 caregiver support nonprofit",
        "digital mental health nonprofit youth",
        "nonprofit veteran peer support organization",
        "501c3 nonprofit AI generated companionship content",
        "nonprofit digital storytelling companionship AI program",
    ],
}


def default_kind_for_brand(brand: Brand) -> ResearchFindingKind:
    if brand == Brand.HEYBUDDY:
        return ResearchFindingKind.NONPROFIT
    if brand in {Brand.CELESTIAL_NEXUS, Brand.MIDNIGHTSATIN, Brand.TACTIC_STUDIO}:
        return ResearchFindingKind.COMPETITOR
    return ResearchFindingKind.OTHER


def seed_queries(
    brand: Brand,
    kind: ResearchFindingKind,
    *,
    explicit_query: str | None = None,
) -> list[str]:
    """Build the bounded query list for a research run."""
    if explicit_query:
        return [explicit_query.strip()]

    if kind == ResearchFindingKind.NONPROFIT:
        return list(NONPROFIT_QUERIES.get(brand, []))

    if kind == ResearchFindingKind.COMPETITOR:
        return list(COMPETITOR_QUERIES.get(brand, []))

    competitor = COMPETITOR_QUERIES.get(brand, [])
    nonprofit = NONPROFIT_QUERIES.get(brand, [])
    return competitor or nonprofit


BRAND_DISPLAY: dict[Brand, str] = {
    Brand.CELESTIAL_NEXUS: "Celestial-Nexus",
    Brand.MIDNIGHTSATIN: "MidnightSatin",
    Brand.HEYBUDDY: "HeyBuddy",
    Brand.TACTIC_STUDIO: "tactic.studio",
    Brand.UNASSIGNED: "unassigned",
}
