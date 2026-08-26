"""Per-brand seed query packs for the outbound hunter loop.

Seeds are data, not hardcoded logic — add packs here or load from config later.
"""

from __future__ import annotations

from agent_crm.enums import Brand

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
    ],
}


def seeds_for_brand(brand: Brand) -> list[str]:
    """Return seed queries for a brand, or an empty list if none configured."""
    return list(SEED_PACKS.get(brand.value, []))
