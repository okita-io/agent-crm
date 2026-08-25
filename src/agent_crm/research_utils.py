"""Shared helpers for the Research agent."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_JUNK_MARKERS = (
    "just a moment",
    "attention required",
    "access denied",
    "cloudflare",
    "please wait",
    "checking your browser",
    "enable javascript",
    "403 forbidden",
    "404 not found",
)

_TRACKING_QUERY_KEYS = frozenset(
    {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
)


def canonical_url(url: str) -> str:
    """Normalize a URL for deduplication."""
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return url.strip()

    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_KEYS
    ]
    query = urlencode(sorted(query_pairs))

    return urlunparse((parsed.scheme.lower(), netloc, path, "", query, ""))


def extract_domain(url: str) -> str:
    """Return a normalized hostname from a URL."""
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_scrapable_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_junk_finding(
    *,
    title: str | None,
    snippet: str | None = None,
    markdown: str | None = None,
) -> bool:
    """Skip Cloudflare interstitials, empty titles, and other junk."""
    if not (title or "").strip():
        return True
    combined = " ".join(
        part
        for part in (
            title or "",
            snippet or "",
            (markdown or "")[:800],
        )
        if part
    ).lower()
    return any(marker in combined for marker in _JUNK_MARKERS)


def clean_title(value: str) -> str:
    text = value.strip()
    text = re.split(r"\s*[|\-–—]\s*", text, maxsplit=1)[0].strip()
    return text[:200] if text else ""


def extract_ein_from_text(text: str) -> str | None:
    """Return an EIN only when explicitly present in source text."""
    match = re.search(r"\b(\d{2}-\d{7})\b", text)
    return match.group(1) if match else None
