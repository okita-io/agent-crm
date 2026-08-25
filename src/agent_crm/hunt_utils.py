"""URL/query normalization and junk filtering for the outbound hunter."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse, urlunparse

from agent_crm.enums import HuntResourceKind

_JUNK_TITLE_PATTERNS = (
    re.compile(r"^just a moment", re.IGNORECASE),
    re.compile(r"^attention required", re.IGNORECASE),
    re.compile(r"^access denied", re.IGNORECASE),
    re.compile(r"^403 forbidden", re.IGNORECASE),
    re.compile(r"^please wait", re.IGNORECASE),
    re.compile(r"^home$", re.IGNORECASE),
    re.compile(r"^index$", re.IGNORECASE),
    re.compile(r"^\s*$"),
)

_JUNK_URL_FRAGMENTS = (
    "challenges.cloudflare.com",
    "/login",
    "/signin",
    "/sign-in",
    "/auth",
    "/account/login",
)

_SOCIAL_HOSTS = {
    "reddit.com",
    "discord.com",
    "discord.gg",
    "twitter.com",
    "x.com",
    "instagram.com",
    "facebook.com",
    "tiktok.com",
    "youtube.com",
    "linkedin.com",
}

_KIND_HINTS: list[tuple[HuntResourceKind, tuple[str, ...]]] = [
    (HuntResourceKind.NEWSLETTER, ("newsletter", "substack", "beehiiv", "mailing list")),
    (HuntResourceKind.FORUM, ("forum", "discourse", "community thread")),
    (HuntResourceKind.COMMUNITY, ("community", "discord", "slack", "group")),
    (HuntResourceKind.DIRECTORY, ("directory", "list of", "database", "catalog")),
    (HuntResourceKind.LIST, ("best ", "top ", "roundup", "listicle")),
    (HuntResourceKind.SOCIAL, ("reddit", "twitter", "instagram", "tiktok")),
]


def normalize_query(query: str) -> str:
    """Collapse whitespace and lowercase for deduplication."""
    return " ".join(query.split()).strip().lower()


def make_dedupe_key(query: str, params: dict | None) -> str:
    """Stable key for queue deduplication."""
    normalized = normalize_query(query)
    if not params:
        return normalized
    parts = [normalized]
    for key in sorted(params):
        parts.append(f"{key}={params[key]}")
    return "|".join(parts)


def canonical_url(url: str) -> str:
    """Normalize a URL for deduplication (scheme/host/path, no fragment)."""
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        parsed = urlparse(f"https://{url.strip()}")
    netloc = parsed.netloc.lower()
    netloc = netloc.removeprefix("www.")
    path = parsed.path.rstrip("/") or ""
    # Drop common tracking params
    query = parse_qs(parsed.query, keep_blank_values=False)
    for tracking in ("utm_source", "utm_medium", "utm_campaign", "fbclid", "gclid"):
        query.pop(tracking, None)
    clean_query = "&".join(f"{k}={query[k][0]}" for k in sorted(query)) if query else ""
    return urlunparse((parsed.scheme or "https", netloc, path, "", clean_query, ""))


def registrable_domain(url: str) -> str:
    """Best-effort registrable domain from a URL."""
    host = urlparse(url).netloc.lower()
    host = host.removeprefix("www.")
    if ":" in host:
        host = host.split(":", 1)[0]
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def is_junk_title(title: str | None) -> bool:
    if not title:
        return True
    cleaned = title.strip()
    if len(cleaned) < 3:
        return True
    return any(pattern.search(cleaned) for pattern in _JUNK_TITLE_PATTERNS)


def is_junk_url(url: str) -> bool:
    lower = url.lower()
    if not lower.startswith(("http://", "https://")):
        return True
    return any(fragment in lower for fragment in _JUNK_URL_FRAGMENTS)


def classify_resource(url: str, title: str | None, snippet: str | None = None) -> HuntResourceKind:
    """Heuristic resource kind from URL/title/snippet."""
    haystack = " ".join(filter(None, [url, title, snippet])).lower()
    host = registrable_domain(url)
    if any(host.endswith(social) or host == social for social in _SOCIAL_HOSTS):
        return HuntResourceKind.SOCIAL
    for kind, hints in _KIND_HINTS:
        if any(hint in haystack for hint in hints):
            return kind
    return HuntResourceKind.OTHER


def extract_heuristic_terms(
    results: list[dict],
    *,
    max_terms: int,
) -> list[str]:
    """Cheap follow-up search terms from titles, snippets, and URLs."""
    seen: set[str] = set()
    terms: list[str] = []

    for result in results:
        title = (result.get("title") or "").strip()
        snippet = (result.get("content") or result.get("snippet") or "").strip()
        url = result.get("url") or ""

        candidates: list[str] = []
        if title and not is_junk_title(title):
            if re.search(r"\b(best|top|list of|communities|forums|newsletters)\b", title, re.IGNORECASE):
                candidates.append(title)
            match = re.search(
                r"(\d+\s+)?(best|top)\s+.+?(communities|forums|newsletters|blogs|discord)",
                title,
                re.IGNORECASE,
            )
            if match:
                candidates.append(match.group(0))

        for text in (snippet, url):
            for match in re.finditer(
                r"(reddit\.com/r/[\w-]+|discord\.gg/[\w-]+|[\w-]+\s+community|[\w-]+\s+forum)",
                text,
                re.IGNORECASE,
            ):
                candidates.append(match.group(0))

        for candidate in candidates:
            normalized = normalize_query(candidate)
            if len(normalized) < 8 or normalized in seen:
                continue
            seen.add(normalized)
            terms.append(candidate.strip())
            if len(terms) >= max_terms:
                return terms

    return terms
