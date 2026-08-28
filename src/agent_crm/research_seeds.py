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
        "rune casting elder futhark divination app",
        "I Ching hexagram reading app",
        "pendulum dowsing divination app",
        "scrying crystal ball reading app",
        "palmistry chiromancy reading app",
        "numerology life path calculator app",
        "oracle card reading app",
        "Lenormand card reading app",
        "tea leaf tasseography reading app",
        "cartomancy playing card divination app",
        "horary astrology reading app",
        "dream interpretation oneiromancy app",
        "geomancy divination reading app",
        "aura reading spiritual app",
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
        "industrial visualization AR experience manufacturing",
        "AR industrial product visualization experience studio",
        "factory floor AR visualization experience",
        "CAD to AR industrial visualization experience",
        "digital twin industrial AR visualization experience",
        "industrial AR training aids manufacturing",
        "AR work instruction training aids factory",
        "mixed reality industrial training aids vendor",
        "enterprise industrial training aid AR overlay",
        "assembly line AR training aid experience",
    ],
}

AD_PLACEMENT_QUERIES: dict[Brand, list[str]] = {
    Brand.MIDNIGHTSATIN: [
        "booktok romance newsletter sponsorship advertising rates",
        "spicy romance subreddit self promotion advertising thread",
        "4chan /lit/ self promotion sticky thread rules",
        "serialized romance discord server advertising sponsorship",
        "romance reader substack newsletter ad placement media kit",
        "wattpad alternative promo board forum banner ads",
        "indie romance author forum sticky thread advertising",
        "romance book club discord server boost sponsorship",
        "bookstagram newsletter sponsorship romance readers",
        "reddit r/RomanceBooks self promotion weekly thread",
        "serial fiction zine newsletter advertising placement",
        "passionflix romance newsletter sponsorship rates",
        "smut readers forum advertising board self promote",
        "interactive fiction community forum banner ads",
        "romance audiobook podcast sponsorship advertising",
        "high traffic romance forums popular weekly threads",
        "most active booktok reddit threads this week",
    ],
    Brand.CELESTIAL_NEXUS: [
        "astrology forum banner advertising media kit",
        "witchtok newsletter sponsorship advertising rates",
        "tarot discord server boost advertising sponsorship",
        "4chan /x/ self promotion occult astrology sticky",
        "horoscope newsletter sponsorship ad placement rates",
        "occult blog advertising media kit sponsorship",
        "witchcraft subreddit self promotion advertising thread",
        "astrology podcast sponsorship advertising rates",
        "tarot reading forum sticky promo board ads",
        "pagan forum banner advertising sponsorship",
        "astro twitter newsletter sponsorship ad placement",
        "divination community discord server advertising",
        "metaphysical magazine advertising rates sponsorship",
        "crystal healing newsletter ad placement media kit",
        "reddit r/astrology weekly self promotion thread",
        "spiritual wellness zine advertising sponsorship",
        "high traffic astrology forums popular weekly threads",
        "most active witchcraft reddit threads this week",
    ],
    Brand.HEYBUDDY: [
        "loneliness forum community resources sticky advertising",
        "veteran mental health forum sponsorship advertising",
        "elder companionship newsletter ad placement sponsorship",
        "501c3 nonprofit newsletter sponsorship advertising rates",
        "caregiver support forum banner ads sponsorship",
        "mental wellness podcast sponsorship advertising rates",
        "veteran peer support forum advertising board",
        "senior living community newsletter ad placement",
        "4chan /adv/ self promotion rules sticky thread",
        "digital wellbeing nonprofit newsletter advertising",
        "loneliness epidemic podcast sponsorship rates",
        "reddit r/lonely community resources wiki advertising",
        "AI companion newsletter sponsorship advertising caution",
        "depression support forum community resources board ads",
        "nonprofit mental health newsletter media kit sponsorship",
        "high traffic loneliness forums popular weekly threads",
        "most active veteran support forum threads this week",
    ],
    Brand.TACTIC_STUDIO: [
        "WebAR newsletter sponsorship advertising rates",
        "industrial training trade publication banner advertising",
        "manufacturing forum banner ads sponsorship media kit",
        "4chan /g/ self promotion WebAR sticky thread",
        "4chan /vr/ advertising self promotion sticky",
        "augmented reality trade magazine ad rates sponsorship",
        "XR industry newsletter sponsorship advertising placement",
        "brand activation blog advertising media kit sponsorship",
        "enterprise AR podcast sponsorship advertising rates",
        "immersive technology forum promo board banner ads",
        "Snap AR lens creator newsletter ad placement",
        "retail CPG innovation newsletter sponsorship rates",
        "industrial metaverse forum advertising sponsorship",
        "WebXR agency blog sponsorship advertising placement",
        "training simulation trade show sponsorship rates",
        "high traffic XR forums popular weekly threads",
        "most active industrial AR reddit threads this week",
    ],
}

TARGET_COMPANY_QUERIES: dict[Brand, list[str]] = {
    Brand.TACTIC_STUDIO: [
        "largest US grocery supermarket chains by revenue",
        "top food and beverage companies over $10 million revenue",
        "largest CPG consumer packaged goods companies United States",
        "biggest restaurant QSR chains United States revenue",
        "convenience store chains US largest companies",
        "largest retail chains United States by annual revenue",
        "supermarket companies over $10 million annual revenue list",
        "beverage brand companies US revenue ranking",
        "regional grocery chains United States list",
        "specialty food retailers largest US companies",
        "department store retail companies United States",
        "largest alcohol beverage companies US",
        "snack food companies US over $10 million revenue",
        "drugstore and convenience retailers largest US chains",
        "foodservice grocery retailers United States companies",
        "Fortune 500 retail grocery food beverage companies",
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


def loop_kinds_for_brand(brand: Brand) -> tuple[ResearchFindingKind, ...]:
    """Kinds the standing research-loop seeds for one brand."""
    if brand == Brand.HEYBUDDY:
        return (ResearchFindingKind.NONPROFIT, ResearchFindingKind.AD_PLACEMENT)
    if brand == Brand.TACTIC_STUDIO:
        return (
            ResearchFindingKind.TARGET_COMPANY,
            ResearchFindingKind.COMPETITOR,
            ResearchFindingKind.AD_PLACEMENT,
        )
    return (ResearchFindingKind.COMPETITOR, ResearchFindingKind.AD_PLACEMENT)


def loop_seed_entries() -> list[tuple[Brand, ResearchFindingKind, str]]:
    """Flatten seed packs the standing loop should enqueue (append-only)."""
    entries: list[tuple[Brand, ResearchFindingKind, str]] = []
    for brand in (
        Brand.CELESTIAL_NEXUS,
        Brand.MIDNIGHTSATIN,
        Brand.HEYBUDDY,
        Brand.TACTIC_STUDIO,
    ):
        for kind in loop_kinds_for_brand(brand):
            for query in seed_queries(brand, kind):
                entries.append((brand, kind, query))
    return entries


def default_kind_for_brand(brand: Brand) -> ResearchFindingKind:
    if brand == Brand.HEYBUDDY:
        return ResearchFindingKind.NONPROFIT
    if brand == Brand.TACTIC_STUDIO:
        return ResearchFindingKind.TARGET_COMPANY
    if brand in {Brand.CELESTIAL_NEXUS, Brand.MIDNIGHTSATIN}:
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

    if kind == ResearchFindingKind.AD_PLACEMENT:
        return list(AD_PLACEMENT_QUERIES.get(brand, []))

    if kind == ResearchFindingKind.TARGET_COMPANY:
        return list(TARGET_COMPANY_QUERIES.get(brand, []))

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
