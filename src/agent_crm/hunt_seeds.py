"""Per-brand seed query packs for the outbound hunter loop.

Seeds are data, not hardcoded logic — add packs here or load from config later.
Audience-tagged packs use origin prefixes ``marketing:``, ``influencer:``, ``user:``
so extraction can inherit the default audience from the query origin.
"""

from __future__ import annotations

from agent_crm.enums import Brand, ContactAudience

SEED_PACKS: dict[str, list[str]] = {
    Brand.MIDNIGHTSATIN.value: [
        "romance booktok communities",
        "dark romance reader newsletter list",
        "book blog directories romance",
        "romance author discord servers",
        "best romance book review blogs",
        "ai generated romance novel communities",
        "ai written spicy romance booktok",
        "chatgpt romance story reddit",
        "ai fiction authors discord",
        "influencers who promote ai generated books",
        "sudowrite novel community",
        "ai generated kindle romance readers",
        "most popular romance book forums high traffic",
        "high engagement booktok reddit communities",
        "busiest romance reader discord servers",
    ],
    Brand.CELESTIAL_NEXUS.value: [
        "astrology natal chart community forums",
        "birth chart discord servers",
        "astrology newsletter directories",
        "zodiac community reddit lists",
        "spiritual astrology blog roundups",
        "ai generated horoscope tiktok creators",
        "ai tarot reading influencers",
        "chatgpt astrology content community",
        "ai natal chart content creators",
        "most active astrology forums high traffic",
        "high engagement tarot reddit communities",
        "busiest witchcraft discord servers",
    ],
    Brand.HEYBUDDY.value: [
        "ai companion app communities",
        "virtual friend discord servers",
        "ai chatbot user forums",
        "loneliness support community lists",
        "ai girlfriend reddit communities",
        "ai companion tiktok influencers",
        "people who watch ai generated character content",
        "ai roleplay discord communities",
        "influencers promoting ai girlfriend apps",
        "most active loneliness support forums high traffic",
        "high engagement mental wellness reddit communities",
        "busiest veteran peer support forums",
    ],
}

# tactic.studio: brand-direct/industrial marketing first; influencers and XR communities second.
SEED_PACKS_BY_AUDIENCE: dict[str, dict[str, list[str]]] = {
    Brand.TACTIC_STUDIO.value: {
        ContactAudience.MARKETING.value: [
            "WebAR brand activation campaign case study",
            "Snap AR lens CPG retail marketing campaign",
            "pack scan AR beverage marketing activation",
            "industrial AR training overlay manufacturing",
            "enterprise AR glasses product visualization",
            "8th Wall WebXR brand experience portfolio",
            "place and scale AR product viz retail",
            "in-store AR beauty retail marketing team",
            "brand marketing director WebAR campaign",
            "CPG marketing augmented reality app launch",
            "manufacturing marketing industrial AR training program",
            "retail brand marketing Snap AR lens campaign",
        ],
        ContactAudience.INFLUENCER.value: [
            "AR glasses reviewer YouTube TikTok",
            "WebAR creator influencer TikTok channel",
            "Snap AR lens creator YouTuber review",
            "industrial XR training reviewer channel",
            "Meta Ray-Ban smart glasses reviewer",
            "Snap Spectacles creator influencer",
            "VR AR headset influencer TikTok",
            "augmented reality tech reviewer YouTube",
            "XR mixed reality content creator channel",
            "WebXR demo creator social media",
        ],
        ContactAudience.USER.value: [
            "reddit augmented reality glasses community",
            "discord WebAR developers server",
            "reddit virtual reality industrial training",
            "lemmy AR glasses discussion forum",
            "Meta Ray-Ban smart glasses reddit",
            "Snap Spectacles users community forum",
            "Android XR developers discord server",
            "WebXR community forum discussion",
            "AR VR training reddit manufacturing",
            "mixed reality enthusiasts discord community",
            "most active WebXR forums high traffic",
            "high engagement industrial AR reddit communities",
        ],
    },
}

_AUDIENCE_PREFIXES: frozenset[str] = frozenset(
    {
        ContactAudience.MARKETING.value,
        ContactAudience.INFLUENCER.value,
        ContactAudience.USER.value,
        ContactAudience.END_USER.value,
        ContactAudience.B2B.value,
        ContactAudience.CLIENT.value,
    }
)


def audience_from_origin(origin: str) -> ContactAudience | None:
    """Parse an audience bucket from a hunt query origin string."""
    from .pipeline_leads import normalize_audience

    for part in origin.split(":"):
        if part in _AUDIENCE_PREFIXES:
            if part == ContactAudience.USER.value:
                return ContactAudience.END_USER
            return normalize_audience(ContactAudience(part))
    return None


def origin_with_audience(base_origin: str, audience: ContactAudience | None) -> str:
    """Prefix ``base_origin`` with an audience when set (e.g. marketing:community:reddit/foo)."""
    if audience is None:
        return base_origin
    if base_origin.startswith(f"{audience.value}:"):
        return base_origin
    return f"{audience.value}:{base_origin}"


def seed_query_entries(brand: Brand) -> list[tuple[str, str]]:
    """Return (query, origin) pairs for enqueueing brand seed packs."""
    audience_pack = SEED_PACKS_BY_AUDIENCE.get(brand.value)
    if audience_pack is not None:
        entries: list[tuple[str, str]] = []
        for audience_key, queries in audience_pack.items():
            origin = f"{audience_key}:seed_pack"
            for query in queries:
                entries.append((query, origin))
        return entries
    return [(query, "seed_pack") for query in SEED_PACKS.get(brand.value, [])]


def seeds_for_brand(brand: Brand) -> list[str]:
    """Return seed queries for a brand, or an empty list if none configured."""
    audience_pack = SEED_PACKS_BY_AUDIENCE.get(brand.value)
    if audience_pack is not None:
        return [query for queries in audience_pack.values() for query in queries]
    return list(SEED_PACKS.get(brand.value, []))
