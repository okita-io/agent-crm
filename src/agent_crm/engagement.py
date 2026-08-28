"""Heuristics for high-engagement venues, popular threads, and rescan queries.

Discovery only: these helpers score and catalog. They never post comments.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .enums import HuntResourceKind
from .hunt_utils import ResourceClassification, registrable_domain

ENGAGEMENT_VENUE_KINDS: frozenset[HuntResourceKind] = frozenset(
    {
        HuntResourceKind.COMMUNITY,
        HuntResourceKind.FORUM,
        HuntResourceKind.SOCIAL,
    }
)

_COUNT_RE = re.compile(
    r"([\d][\d,]*)\s*(members?|subscribers?|users?|readers?|comments?|replies?|"
    r"upvotes?|points?|likes?|karmas?)\b",
    re.IGNORECASE,
)

_HOT_HINTS = (
    "hot",
    "trending",
    "most popular",
    "most active",
    "high traffic",
    "high engagement",
    "active community",
    "weekly thread",
    "megathread",
    "front page",
)

_TREND_HINTS = (
    "weekly",
    "daily",
    "megathread",
    "sticky",
    "hot",
    "trending",
    "ama",
    "self promotion",
    "self-promotion",
    "what are you reading",
    "recommendation",
)

_THREAD_PATTERNS = (
    re.compile(r"reddit\.com/r/[^/]+/comments/", re.IGNORECASE),
    re.compile(r"/t/[A-Za-z0-9\-]+/\d+", re.IGNORECASE),
    re.compile(r"/threads?/", re.IGNORECASE),
    re.compile(r"/topics?/", re.IGNORECASE),
    re.compile(r"showthread\.php", re.IGNORECASE),
    re.compile(r"viewtopic\.php", re.IGNORECASE),
    re.compile(r"/post/\d+", re.IGNORECASE),
    re.compile(r"(?:twitter|x)\.com/[^/]+/status/", re.IGNORECASE),
    re.compile(r"facebook\.com/.+/posts/", re.IGNORECASE),
    re.compile(r"facebook\.com/groups/[^/]+/posts/", re.IGNORECASE),
    re.compile(r"linkedin\.com/posts/", re.IGNORECASE),
    re.compile(r"tiktok\.com/.+/video/", re.IGNORECASE),
    re.compile(r"youtube\.com/watch", re.IGNORECASE),
    re.compile(r"lobste\.rs/s/", re.IGNORECASE),
)


@dataclass(frozen=True)
class EngagementSignals:
    """Parsed popularity signals from a title, snippet, or page body."""

    score: int
    comment_count: int | None = None
    member_count: int | None = None
    signals: tuple[str, ...] = ()
    trend_keywords: tuple[str, ...] = ()


def extract_engagement_signals(
    title: str | None,
    snippet: str | None,
    markdown: str | None = None,
    *,
    kind: HuntResourceKind | None = None,
) -> EngagementSignals:
    """Score traffic/engagement hints without inventing counts."""
    haystack = " ".join(part for part in (title, snippet, markdown) if part)
    lower = haystack.lower()
    score = 0
    signals: list[str] = []
    member_count: int | None = None
    comment_count: int | None = None

    if kind in ENGAGEMENT_VENUE_KINDS:
        score += 10
        signals.append(kind.value)

    for hint in _HOT_HINTS:
        if hint in lower:
            score += 15 if hint in {"hot", "trending", "most popular"} else 10
            signals.append(hint)

    for match in _COUNT_RE.finditer(haystack):
        raw, unit = match.group(1), match.group(2).lower()
        count = _parse_count(raw)
        if count is None:
            continue
        if unit.startswith(("member", "subscriber", "user", "reader")):
            member_count = max(member_count or 0, count)
        elif unit.startswith(("comment", "repl")):
            comment_count = max(comment_count or 0, count)
        else:
            comment_count = max(comment_count or 0, count)

    if member_count:
        score += min(40, int(10 * math.log10(member_count + 1)))
        signals.append(f"{member_count}_members")
    if comment_count:
        score += min(30, comment_count // 10)
        signals.append(f"{comment_count}_comments")

    score = max(0, min(100, score))
    trends = tuple(hint for hint in _TREND_HINTS if hint in lower)
    return EngagementSignals(
        score=score,
        comment_count=comment_count,
        member_count=member_count,
        signals=tuple(dict.fromkeys(signals)),
        trend_keywords=trends,
    )


def _parse_count(raw: str) -> int | None:
    cleaned = raw.replace(",", "").strip()
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def is_thread_url(url: str) -> bool:
    """True when the URL looks like a specific post rather than a venue listing."""
    lower = url.lower()
    return any(pattern.search(lower) for pattern in _THREAD_PATTERNS)


def is_engagement_venue(classification: ResourceClassification, url: str) -> bool:
    """True when a catalogued resource is worth later popular-post scans."""
    if is_thread_url(url):
        return False
    return classification.kind in ENGAGEMENT_VENUE_KINDS


def engagement_payload(signals: EngagementSignals) -> dict:
    """JSON-serializable engagement block for hunt_resources.notes."""
    payload: dict[str, object] = {"score": signals.score}
    if signals.signals:
        payload["signals"] = list(signals.signals)
    if signals.comment_count is not None:
        payload["comment_count"] = signals.comment_count
    if signals.member_count is not None:
        payload["member_count"] = signals.member_count
    if signals.trend_keywords:
        payload["trend_keywords"] = list(signals.trend_keywords)
    return payload


def venue_scan_queries(
    classification: ResourceClassification,
    *,
    url: str,
    max_terms: int = 4,
) -> list[str]:
    """Search terms that surface popular posts on a catalogued venue."""
    slug = classification.community_slug
    platform = classification.platform
    label = classification.community_label
    domain = registrable_domain(url)
    terms: list[str] = []

    if platform == "reddit" and slug:
        terms.extend(
            [
                f"site:reddit.com/r/{slug} hot",
                f"site:reddit.com/r/{slug} top this week",
                f"site:reddit.com/r/{slug} weekly thread",
            ]
        )
    elif platform == "facebook" and slug:
        terms.extend(
            [
                f"site:facebook.com/groups/{slug} popular posts",
                f'"{slug}" facebook group trending',
            ]
        )
    elif platform == "discord" and slug:
        terms.extend(
            [
                f"{slug} discord popular channels",
                f'"{slug}" discord discussion',
            ]
        )
    elif platform in {"discourse", "lemmy", "lobsters"} and slug:
        terms.extend(
            [
                f"{slug} {platform} popular threads",
                f'site:{domain} top topics',
            ]
        )
    elif domain:
        terms.extend(
            [
                f"site:{domain} popular threads",
                f"site:{domain} trending discussion",
                f"site:{domain} hot posts",
            ]
        )

    if label:
        quoted = f'"{label.strip()}"'
        extra = f"{quoted} popular thread"
        if extra not in terms:
            terms.append(extra)

    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = " ".join(term.split()).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(term.strip())
        if len(deduped) >= max_terms:
            break
    return deduped


def venue_url_from_thread(url: str, classification: ResourceClassification) -> str | None:
    """Best-effort listing URL for the venue that owns a thread."""
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path
    slug = classification.community_slug
    if classification.platform == "reddit" and slug:
        return f"https://reddit.com/r/{slug}"
    if classification.platform == "facebook" and slug:
        return f"https://facebook.com/groups/{slug}"
    if classification.platform == "discourse":
        return f"{parsed.scheme or 'https'}://{host}"
    reddit = re.search(r"/r/([A-Za-z0-9_]+)", path)
    if "reddit.com" in host and reddit:
        return f"https://reddit.com/r/{reddit.group(1)}"
    if host:
        return f"{parsed.scheme or 'https'}://{host}"
    return None
