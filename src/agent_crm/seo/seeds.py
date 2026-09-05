"""Seed SEO targets: ranch-owned sites plus named search competitors.

Documents only. These URLs are scraped and written about; they are never patched.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_crm.enums import Brand, SeoQueryKind, SeoTargetRole


@dataclass(frozen=True)
class SeoSeed:
    url: str
    role: SeoTargetRole
    title: str
    notes: str = ""
    query_kind: SeoQueryKind = SeoQueryKind.SITE_AUDIT


BRAND_DISPLAY: dict[Brand, str] = {
    Brand.MIDNIGHTSATIN: "MidnightSatin",
    Brand.CELESTIAL_NEXUS: "Celestial-Nexus",
    Brand.HEYBUDDY: "HeyBuddy",
    Brand.TACTIC_STUDIO: "tactic.studio",
    Brand.BEST_BIRYANI: "Best Biryani",
}

# Owned sites get a site-audit review plus an implementation plan.
# Competitor sites get a competitor review only (never a plan to change them).
SEO_SEEDS: dict[Brand, tuple[SeoSeed, ...]] = {
    Brand.MIDNIGHTSATIN: (
        SeoSeed(
            url="https://midnightsatin.app",
            role=SeoTargetRole.OWNED,
            title="MidnightSatin",
            notes="Ranch-owned romance reading app (midnightSatin.app).",
        ),
        SeoSeed(
            url="https://www.galatea.com",
            role=SeoTargetRole.COMPETITOR,
            title="Galatea",
            notes="Named competitor: serialized romance reading app.",
            query_kind=SeoQueryKind.COMPETITOR,
        ),
        SeoSeed(
            url="https://www.dreame.com",
            role=SeoTargetRole.COMPETITOR,
            title="Dreame",
            notes="Named competitor: serialized romance fiction.",
            query_kind=SeoQueryKind.COMPETITOR,
        ),
        SeoSeed(
            url="https://www.radishfiction.com",
            role=SeoTargetRole.COMPETITOR,
            title="Radish Fiction",
            notes="Named competitor: serialized fiction app.",
            query_kind=SeoQueryKind.COMPETITOR,
        ),
        SeoSeed(
            url="https://www.wattpad.com",
            role=SeoTargetRole.COMPETITOR,
            title="Wattpad",
            notes="Named search competitor: user-upload romance serials (not a clone target).",
            query_kind=SeoQueryKind.COMPETITOR,
        ),
        SeoSeed(
            url="https://www.passionflix.com",
            role=SeoTargetRole.COMPETITOR,
            title="Passionflix",
            notes="Named search competitor: romance adaptation / serial-adjacent media.",
            query_kind=SeoQueryKind.COMPETITOR,
        ),
    ),
    Brand.CELESTIAL_NEXUS: (
        SeoSeed(
            url="https://divinationnexus.com",
            role=SeoTargetRole.OWNED,
            title="Divination Nexus",
            notes="Ranch-owned astrology and divination reading app (was celestial-nexus.app).",
        ),
        SeoSeed(
            url="https://www.costarastrology.com",
            role=SeoTargetRole.COMPETITOR,
            title="Co-Star",
            notes="Named competitor: natal chart / astrology app.",
            query_kind=SeoQueryKind.COMPETITOR,
        ),
        SeoSeed(
            url="https://www.thepattern.com",
            role=SeoTargetRole.COMPETITOR,
            title="The Pattern",
            notes="Named competitor: astrology personality app.",
            query_kind=SeoQueryKind.COMPETITOR,
        ),
        SeoSeed(
            url="https://www.chani.com",
            role=SeoTargetRole.COMPETITOR,
            title="CHANI",
            notes="Named competitor: astrology app.",
            query_kind=SeoQueryKind.COMPETITOR,
        ),
    ),
    Brand.HEYBUDDY: (
        SeoSeed(
            url="https://heybuddy.app",
            role=SeoTargetRole.OWNED,
            title="HeyBuddy",
            notes="Ranch-owned AI companion (not a nonprofit).",
        ),
        SeoSeed(
            url="https://replika.com",
            role=SeoTargetRole.COMPETITOR,
            title="Replika",
            notes="Named competitor: AI companion app.",
            query_kind=SeoQueryKind.COMPETITOR,
        ),
        SeoSeed(
            url="https://character.ai",
            role=SeoTargetRole.COMPETITOR,
            title="Character.AI",
            notes="Named competitor: AI companion / character chat.",
            query_kind=SeoQueryKind.COMPETITOR,
        ),
    ),
    Brand.TACTIC_STUDIO: (
        SeoSeed(
            url="https://tactic.studio",
            role=SeoTargetRole.OWNED,
            title="tactic.studio",
            notes="Ranch-owned WebAR / industrial AR training vendor.",
        ),
        SeoSeed(
            url="https://www.8thwall.com",
            role=SeoTargetRole.COMPETITOR,
            title="8th Wall",
            notes="Platform adjacent: WebAR engine often compared in search.",
            query_kind=SeoQueryKind.COMPETITOR,
        ),
        SeoSeed(
            url="https://www.zappar.com",
            role=SeoTargetRole.COMPETITOR,
            title="Zappar",
            notes="Named search competitor: WebAR / immersive studio.",
            query_kind=SeoQueryKind.COMPETITOR,
        ),
    ),
    Brand.BEST_BIRYANI: (
        SeoSeed(
            url="https://bestbiryanisiliconvalley.com",
            role=SeoTargetRole.OWNED,
            title="Best Biryani Silicon Valley",
            notes="Local restaurant destination (brand context not vendored yet).",
        ),
    ),
}

KEYWORD_SEEDS: dict[Brand, tuple[str, ...]] = {
    Brand.MIDNIGHTSATIN: (
        "AI romance serial app",
        "serialized romance reading app",
        "spicy romance stories mobile",
        "BookTok romance serial",
        "dark romance serialized fiction",
    ),
    Brand.CELESTIAL_NEXUS: (
        "natal chart app",
        "tarot reading app",
        "divination app tarot astrology",
    ),
    Brand.HEYBUDDY: (
        "AI companion for loneliness",
        "wellness companion chat app",
        "supportive AI check-in app",
    ),
    Brand.TACTIC_STUDIO: (
        "industrial AR training",
        "WebAR brand activation studio",
        "8th Wall industrial visualization",
    ),
    Brand.BEST_BIRYANI: (
        "biryani restaurant Silicon Valley",
        "best biryani Bay Area",
        "Indian restaurant Sunnyvale biryani",
    ),
}


def seeds_for_brand(brand: Brand) -> tuple[SeoSeed, ...]:
    return SEO_SEEDS.get(brand, ())


def keyword_seeds_for_brand(brand: Brand) -> tuple[str, ...]:
    return KEYWORD_SEEDS.get(brand, ())
