"""Follow-up search terms extracted from research SERP hits and scraped pages."""

from __future__ import annotations

import re
from typing import Any

from agent_crm.enums import Brand, ResearchFindingKind
from agent_crm.hunt.utils import extract_heuristic_terms, is_junk_title, normalize_query

BRAND_TOPIC_HINTS: dict[Brand, tuple[str, ...]] = {
    Brand.CELESTIAL_NEXUS: (
        "tarot",
        "runes",
        "i ching",
        "pendulum",
        "scrying",
        "palmistry",
        "numerology",
        "oracle cards",
        "lenormand",
        "tea leaf",
        "tasseography",
        "aura reading",
        "ogham",
        "bibliomancy",
        "geomancy",
        "cartomancy",
        "horary",
        "synastry",
        "vedic astrology",
        "crystal ball",
        "dowsing",
        "dream interpretation",
        "cowrie",
        "rune casting",
    ),
    Brand.MIDNIGHTSATIN: (
        "serialized romance",
        "booktok",
        "kindle unlimited",
        "wattpad",
        "galatea",
        "radish",
        "dreame",
        "spicy romance",
        "romantasy",
        "interactive romance",
        "romance audiobook",
        "ai generated romance",
        "bookstagram",
    ),
    Brand.HEYBUDDY: (
        "loneliness",
        "elder companionship",
        "veteran mental health",
        "501c3",
        "caregiver support",
        "youth digital wellbeing",
        "peer support",
        "social isolation",
        "ai companion",
        "elder isolation",
    ),
    Brand.TACTIC_STUDIO: (
        "industrial visualization",
        "product visualization",
        "industrial training aid",
        "training aids",
        "digital twin",
        "work instruction",
        "webar",
        "webxr",
        "factory ar",
        "cad visualization",
        "assembly overlay",
        "mixed reality training",
        "industrial ar visualization",
    ),
}

_TITLE_KEEP = re.compile(
    r"\b(app|studio|agency|nonprofit|501|forum|newsletter|community|"
    r"discord|reddit|podcast|visualization|training|divination|romance|"
    r"grocery|supermarket|retail|beverage|restaurant)\b",
    re.IGNORECASE,
)

_TARGET_COMPANY_HINTS: tuple[str, ...] = (
    "grocery",
    "supermarket",
    "convenience store",
    "cpg",
    "restaurant chain",
    "beverage company",
    "food retailer",
    "department store",
)


def follow_up_suffix(brand: Brand, kind: ResearchFindingKind) -> str:
    """Kind-aware suffix so extracted hints become runnable search queries."""
    if kind == ResearchFindingKind.AD_PLACEMENT:
        return "sponsorship advertising"
    if kind == ResearchFindingKind.NONPROFIT:
        return "501c3 nonprofit"
    if kind == ResearchFindingKind.TARGET_COMPANY:
        return "retail companies over $10 million revenue"
    if brand == Brand.TACTIC_STUDIO:
        return "AR experience studio"
    if brand == Brand.CELESTIAL_NEXUS:
        return "divination app"
    if brand == Brand.MIDNIGHTSATIN:
        return "romance app"
    if brand == Brand.HEYBUDDY:
        return "companionship nonprofit"
    return "research"


def extract_research_follow_up_terms(
    *,
    query: str,
    brand: Brand,
    kind: ResearchFindingKind,
    serp_results: list[dict[str, Any]],
    page_texts: list[str],
    max_terms: int,
) -> list[str]:
    """Build new search queries from SearXNG hits and Firecrawl page text.

    The research queue is append-only; callers enqueue whatever this returns
    after dedupe. Never invents person names or emails.
    """
    if max_terms <= 0:
        return []

    original = normalize_query(query)
    combined = " ".join(
        part
        for part in (
            *(str(item.get("title") or "") for item in serp_results),
            *(str(item.get("content") or item.get("snippet") or "") for item in serp_results),
            *page_texts,
        )
        if part
    ).lower()

    candidates: list[str] = []
    suffix = follow_up_suffix(brand, kind)
    hints = BRAND_TOPIC_HINTS.get(brand, ())
    if kind == ResearchFindingKind.TARGET_COMPANY:
        hints = _TARGET_COMPANY_HINTS
    for hint in hints:
        if hint not in combined:
            continue
        if hint in original:
            continue
        candidates.append(f"{hint} {suffix}")

    for item in serp_results:
        title = (item.get("title") or "").strip()
        if not title or is_junk_title(title):
            continue
        if _TITLE_KEEP.search(title):
            candidates.append(title)

    candidates.extend(extract_heuristic_terms(serp_results, max_terms=max_terms))

    merged: list[str] = []
    seen: set[str] = {original}
    for term in candidates:
        cleaned = " ".join(term.split()).strip()
        key = normalize_query(cleaned)
        if len(key) < 8 or key in seen:
            continue
        seen.add(key)
        merged.append(cleaned[:200])
        if len(merged) >= max_terms:
            break
    return merged
