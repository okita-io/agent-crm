"""SearXNG-based social profile lookup for contact emails (no paid APIs)."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from .config import get_settings
from .searxng_client import SearchResult, search

PROFILE_PATH_HINTS = {
    "x": ("/",),
    "linkedin": ("/in/",),
    "instagram": ("/",),
    "facebook": ("/",),
}


def _normalize_profile_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return url.strip()
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def _platform_from_url(url: str) -> str | None:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    if host in {"x.com", "twitter.com"}:
        return "x"
    if host == "linkedin.com" and "/in/" in url.lower():
        return "linkedin"
    if host == "instagram.com":
        return "instagram"
    if host == "facebook.com":
        return "facebook"
    return None


def _matches_contact(
    *,
    email: str,
    name: str | None,
    url: str,
    title: str,
    snippet: str,
) -> bool:
    haystack = f"{url} {title} {snippet}".lower()
    local = email.split("@", 1)[0].lower()
    if email.lower() in haystack or local in haystack:
        return True
    if name:
        name_lower = name.lower()
        if name_lower in haystack:
            return True
        parts = [part for part in re.split(r"\s+", name_lower) if len(part) > 2]
        if len(parts) >= 2 and all(part in haystack for part in parts[:2]):
            return True
    handle = urlparse(url).path.strip("/").split("/")[-1].lower()
    if handle and handle.replace("-", "").replace("_", "") == local.replace(".", ""):
        return True
    return False


def _pick_profile_hit(
    results: list[SearchResult],
    *,
    platform: str,
    email: str,
    name: str | None,
) -> str | None:
    for hit in results:
        platform_guess = _platform_from_url(hit.url)
        if platform_guess != platform:
            continue
        if not _matches_contact(
            email=email, name=name, url=hit.url, title=hit.title, snippet=hit.snippet
        ):
            continue
        return _normalize_profile_url(hit.url)
    return None


def build_social_queries(email: str, name: str | None) -> list[str]:
    """Build a bounded search pack for one contact profile."""
    settings = get_settings()
    cap = settings.contact_social_queries_per_profile
    queries: list[str] = [f'"{email}"']

    if name:
        queries.append(f'"{name}" site:x.com OR site:twitter.com')
        queries.append(f'"{name}" site:linkedin.com/in')
        queries.append(f'"{name}" site:instagram.com')
    else:
        queries.append(f'"{email}" site:linkedin.com/in')
        queries.append(f'"{email}" site:x.com OR site:twitter.com')

    return queries[:cap]


def lookup_social_profiles(
    *,
    email: str,
    name: str | None,
    client: httpx.Client | None = None,
    max_queries: int | None = None,
) -> tuple[dict[str, str | list[str]], int]:
    """Run SearXNG searches and return clearly matching public profile URLs.

    Returns ``(socials, queries_used)``.
    """
    settings = get_settings()
    query_cap = max_queries if max_queries is not None else settings.contact_social_queries_per_profile
    queries = build_social_queries(email, name)[:query_cap]

    socials: dict[str, str] = {}
    queries_used = 0

    platform_for_query = [
        None,
        "x",
        "linkedin",
        "instagram",
    ]

    for index, query in enumerate(queries):
        platform = platform_for_query[index] if index < len(platform_for_query) else None
        results = search(query, limit=8, client=client)
        queries_used += 1

        if platform:
            hit = _pick_profile_hit(results, platform=platform, email=email, name=name)
            if hit and platform not in socials:
                socials[platform] = hit
            continue

        for hit in results:
            platform_guess = _platform_from_url(hit.url)
            if platform_guess is None:
                continue
            if platform_guess in socials:
                continue
            if _matches_contact(
                email=email,
                name=name,
                url=hit.url,
                title=hit.title,
                snippet=hit.snippet,
            ):
                socials[platform_guess] = _normalize_profile_url(hit.url)

    return socials, queries_used
