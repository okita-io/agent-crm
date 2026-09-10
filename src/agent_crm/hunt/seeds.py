"""Per-brand seed query packs for the outbound hunter loop.

Seeds are data, not hardcoded logic — add packs here or load from config later.
Audience-tagged packs use origin prefixes ``marketing:``, ``influencer:``, ``user:``
so extraction can inherit the default audience from the query origin.
"""

from __future__ import annotations

from agent_crm.enums import Brand, ContactAudience

HUNT_LOOP_BRANDS: tuple[Brand, ...] = (
    Brand.CELESTIAL_NEXUS,
    Brand.MIDNIGHTSATIN,
    Brand.HEYBUDDY,
    Brand.TACTIC_STUDIO,
)


def hunt_loop_brands() -> tuple[Brand, ...]:
    from agent_crm.projects.channel_flags import active_brands_for

    return active_brands_for("hunter") or HUNT_LOOP_BRANDS


def loop_seed_entries() -> list[tuple[Brand, str, str]]:
    """Flatten seed packs the standing hunt-loop should enqueue (append-only)."""
    from agent_crm.projects.mission import supplemental_seed_queries

    entries: list[tuple[Brand, str, str]] = []
    for brand in hunt_loop_brands():
        for query, origin in seed_query_entries(brand):
            entries.append((brand, query, origin))
        for query in supplemental_seed_queries(brand, "hunter"):
            entries.append((brand, query, "prompt_seed:hunter"))
    return entries

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
        "site:publishersweekly.com romance",
        "site:kirkusreviews.com romance",
        "site:bookriot.com romance",
        "site:smartbitchestrashybooks.com",
        "site:shereads.com romance",
        "site:bookpage.com romance",
        "reddit.com/r/RomanceBooks",
        "reddit.com/r/RomanceClub",
        "reddit.com/r/fantasyromance",
        "reddit.com/r/wattpad romance",
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
        "site:theastrologypodcast.com",
        "site:astrologyhub.com",
        "reddit.com/r/astrology",
        "reddit.com/r/Advancedastrology",
        "reddit.com/r/witchcraft",
    ],
    Brand.BEST_BIRYANI.value: [
        "best biryani Silicon Valley restaurant reviews",
        "Bay Area biryani food blogs",
        "Indian restaurant Sunnyvale community forums",
        "site:yelp.com biryani Silicon Valley",
        "reddit.com/r/bayarea biryani",
    ],
}

# HeyBuddy: grant-awarded loneliness/veteran/elder/caregiver partners first.
# tactic.studio: grant-awarded museum/campus/cultural institution contacts first;
# XR creators covering immersive cultural work second.
SEED_PACKS_BY_AUDIENCE: dict[str, dict[str, list[str]]] = {
    Brand.HEYBUDDY.value: {
        ContactAudience.MARKETING.value: [
            "SAMHSA loneliness social isolation grant awardee leadership",
            "ACL elder isolation grant award recipient contact",
            "VA veteran mental health grant awardee program director",
            "Grants.gov social isolation community grant recipient",
            "USASpending veteran peer support grant awardee",
            "HHS caregiver support grant award contact",
            "Administration for Community Living senior isolation grant",
            "HRSA rural mental health grant awardee leadership",
            "Older Americans Act Title III grant recipient aging agency",
            "state aging agency social isolation grant award contact",
            "youth digital wellbeing SAMHSA grant recipient leadership",
            "501c3 federal grant loneliness program director",
            "Area Agency on Aging grant award partnership contact",
            "CDC social connectedness grant awardee leadership",
        ],
        ContactAudience.INFLUENCER.value: [
            "veteran mental health grant program advocate channel",
            "loneliness epidemic federal grant researcher speaker",
            "caregiver support grant funded program creator",
            "elder isolation ACL grant project documentary",
            "youth digital wellbeing SAMHSA grant showcase creator",
        ],
        ContactAudience.USER.value: [
            "reddit loneliness grant funded community programs",
            "veteran peer support grant recipient community forum",
            "caregiver support grant program community",
            "elder isolation Area Agency on Aging community",
            "social isolation SAMHSA grant community forum",
            "most active veteran peer support forums high traffic",
            "high engagement loneliness support reddit communities",
            "busiest caregiver support forums high traffic",
        ],
    },
    Brand.TACTIC_STUDIO.value: {
        ContactAudience.MARKETING.value: [
            "IMLS museum interactive exhibit grant awardee leadership team",
            "NEH digital humanities grant university museum contact",
            "NEA arts grant immersive exhibit recipient institution",
            "museum director of exhibits digital media team page",
            "university immersive media center grant project lead",
            "library digital experience grant award contact marketing",
            "campus AR XR immersive project grant recipient leadership",
            "Grants.gov museum interactive exhibit award recipient",
            "USASpending museum immersive digital media grant awardee",
            "state arts council interactive exhibit grant museum recipient",
            "cultural institution digital storytelling grant award contact",
            "museum experience production director team page",
            "university museum marketing exhibits digital media contact",
            "science center immersive exhibit grant awardee leadership",
            "grants.ca.gov Quantum FAST challenge award recipient director",
            "UCLA CQSE MQST Ross quantum Program Director OR Chair",
            "UC Berkeley CIQC Sipahigil quantum Lab Director",
            "UCSB Moody quantum education Director OR Chair",
            "UC Davis Radulaski quantum Lab Director OR Program Director",
            "UC San Diego Sailor Brydges quantum Director OR Chair",
            "San Jose State SJSU Hurst quantum QIST Program Director",
            "Cal State San Marcos CSUSM Perron quantum Director",
            "Cal Poly Gillen quantum QIST Program Director OR Chair",
            "San Francisco State SFSU Bethel quantum Director OR Chair",
            "Cal State LA Mondin quantum Director OR Chair",
            "CSU East Bay Kimball quantum Director OR Chair",
            "Foothill College ETI quantum workforce Director OR Coordinator",
            "Director of Exhibits OR Director of Experience California university museum",
            "Director of Digital Media OR Director of Innovation California campus",
            "Workforce Development Director quantum education California university",
            "VP Marketing OR CMO university museum California grant",
        ],
        ContactAudience.INFLUENCER.value: [
            "museum AR exhibit creator YouTube channel",
            "campus immersive media project documentary creator",
            "cultural institution digital experience case study creator",
            "WebAR museum exhibit reviewer YouTube",
            "immersive storytelling museum installation creator TikTok",
            "university XR lab project showcase creator",
            "interactive exhibit design influencer channel",
            "museum technology digital media creator blog",
            "NEA grant immersive arts project showcase creator",
            "campus digital twin immersive experience creator",
        ],
        ContactAudience.USER.value: [
            "reddit museum technology interactive exhibits community",
            "discord museum digital media professionals server",
            "reddit university immersive media XR projects",
            "museum exhibit design professionals forum",
            "campus experience design immersive media community",
            "library digital experience makers forum",
            "cultural heritage AR XR practitioners community",
            "interactive museum exhibit professionals linkedin group",
            "immersive storytelling cultural institutions forum",
            "most active museum technology forums high traffic",
            "high engagement campus XR immersive media reddit",
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
    from agent_crm.contacts.pipeline_leads import normalize_audience

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
