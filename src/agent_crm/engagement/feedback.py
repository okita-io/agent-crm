"""Follow-up search terms extracted from engagement SERP hits and scraped pages."""

from __future__ import annotations

import re
from typing import Any

from agent_crm.enums import Brand
from agent_crm.hunt.utils import extract_heuristic_terms, is_junk_title, normalize_query
from agent_crm.research.feedback import BRAND_TOPIC_HINTS

_REDDIT_SUB_RE = re.compile(
    r"(?:reddit\.com/r/|\br/)([A-Za-z0-9_]{2,30})", re.IGNORECASE
)
_DISCORD_RE = re.compile(
    r"(?:discord\.gg/|discord\.com/invite/)([A-Za-z0-9\-]+)", re.IGNORECASE
)
_TITLE_KEEP = re.compile(
    r"\b(megathread|weekly thread|forum|community|discord|reddit|subreddit|"
    r"facebook group|hot|trending|popular)\b",
    re.IGNORECASE,
)


def community_follow_up_suffix(brand: Brand) -> str:
    if brand == Brand.TACTIC_STUDIO:
        return "reddit community"
    if brand == Brand.CELESTIAL_NEXUS:
        return "reddit discord community"
    if brand == Brand.MIDNIGHTSATIN:
        return "reddit booktok community"
    if brand == Brand.HEYBUDDY:
        return "support forum community"
    return "community forum"


def extract_engagement_follow_up_terms(
    *,
    query: str,
    brand: Brand,
    serp_results: list[dict[str, Any]],
    page_texts: list[str],
    max_terms: int,
) -> list[str]:
    """Build new venue/thread search queries from SearXNG hits and Firecrawl pages.

    The engagement queue is append-only; callers enqueue whatever this returns
    after dedupe. Never invents person names or emails.
    """
    if max_terms <= 0:
        return []

    original = normalize_query(query)
    combined = " ".join(
        part
        for part in (
            *(str(item.get("title") or "") for item in serp_results),
            *(str(item.get("url") or "") for item in serp_results),
            *(str(item.get("content") or item.get("snippet") or "") for item in serp_results),
            *page_texts,
        )
        if part
    )

    candidates: list[str] = []
    suffix = community_follow_up_suffix(brand)

    seen_subs: set[str] = set()
    for match in _REDDIT_SUB_RE.finditer(combined):
        slug = match.group(1)
        key = slug.lower()
        if key in seen_subs or key in original:
            continue
        seen_subs.add(key)
        candidates.append(f"site:reddit.com/r/{slug} hot")
        candidates.append(f"site:reddit.com/r/{slug} top this week")

    for match in _DISCORD_RE.finditer(combined):
        invite = match.group(1)
        if invite.lower() in original:
            continue
        candidates.append(f"{invite} discord community")

    lower_combined = combined.lower()
    for hint in BRAND_TOPIC_HINTS.get(brand, ()):
        if hint not in lower_combined or hint in original:
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
