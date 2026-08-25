"""Deterministic contact extraction from scraped page markdown and HTML."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

NAME_EMAIL_ANGLE_RE = re.compile(
    r"([A-Z][a-zA-Z'. \-]{1,80}?)\s*<([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>"
)

MAILTO_MD_RE = re.compile(
    r"\[([^\]]+)\]\(mailto:([^)\s]+)\)",
    re.IGNORECASE,
)

MAILTO_HTML_RE = re.compile(
    r'<a[^>]+href=["\']mailto:([^"\']+)["\'][^>]*>([^<]+)</a>',
    re.IGNORECASE,
)

SOCIAL_URL_PATTERNS: dict[str, re.Pattern[str]] = {
    "x": re.compile(
        r"https?://(?:www\.)?(?:x\.com|twitter\.com)/[A-Za-z0-9_%-]+/?",
        re.IGNORECASE,
    ),
    "linkedin": re.compile(
        r"https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9_%-]+/?",
        re.IGNORECASE,
    ),
    "instagram": re.compile(
        r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.%-]+/?",
        re.IGNORECASE,
    ),
    "facebook": re.compile(
        r"https?://(?:www\.)?facebook\.com/[A-Za-z0-9_.%-]+/?",
        re.IGNORECASE,
    ),
}

SKIP_LOCAL_PARTS = (
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "privacy",
    "mailer-daemon",
    "notifications",
)

SKIP_DOMAIN_FRAGMENTS = (
    "example.com",
    "sentry.io",
    "wixpress.com",
    "cloudflare.com",
    "github.com",
)

SKIP_EMAIL_FRAGMENTS = ("githubnoreply", "wixpress")

GENERIC_NAME_WORDS = frozenset(
    {
        "a",
        "an",
        "at",
        "contact",
        "email",
        "for",
        "general",
        "hello",
        "hi",
        "inquiries",
        "inquiry",
        "mail",
        "message",
        "or",
        "our",
        "reach",
        "sales",
        "send",
        "support",
        "team",
        "the",
        "to",
        "us",
        "write",
        "your",
    }
)


@dataclass
class ExtractedContact:
    """One email contact found on a page."""

    email: str
    name: str | None = None
    socials: dict[str, str | list[str]] = field(default_factory=dict)


def normalize_email(email: str) -> str:
    return unquote(email.strip()).lower().rstrip(".,;)")


def is_skipped_email(email: str) -> bool:
    normalized = normalize_email(email)
    if not normalized or "@" not in normalized:
        return True
    local, _, domain = normalized.partition("@")
    if not local or not domain:
        return True
    for fragment in SKIP_EMAIL_FRAGMENTS:
        if fragment in normalized:
            return True
    for part in SKIP_LOCAL_PARTS:
        if local == part or local.startswith(f"{part}+"):
            return True
    for fragment in SKIP_DOMAIN_FRAGMENTS:
        if domain == fragment or domain.endswith(f".{fragment}"):
            return True
    if domain.endswith(".example") or domain.endswith(".invalid"):
        return True
    return False


def _looks_like_name(value: str) -> bool:
    text = value.strip()
    if not text or "@" in text or len(text) > 80:
        return False
    if EMAIL_RE.search(text):
        return False
    if text.lower().startswith(("http://", "https://", "www.")):
        return False
    if re.fullmatch(r"[\d\s\W]+", text):
        return False
    words = re.findall(r"[A-Za-z]+", text)
    if not words or len(words) > 5:
        return False
    lowered = [word.lower() for word in words]
    if all(word in GENERIC_NAME_WORDS for word in lowered):
        return False
    if any(word in GENERIC_NAME_WORDS for word in lowered) and not any(
        re.fullmatch(r"[A-Z][a-z'\-]{1,}", word) for word in words
    ):
        return False
    titled = [word for word in words if re.fullmatch(r"[A-Z][a-z'\-]{1,}", word)]
    return len(titled) >= 1


def _clean_mailto_name(value: str) -> str | None:
    text = re.sub(r"\s+", " ", value.strip())
    text = text.strip(".,;:-")
    if not text or "@" in text or len(text) > 80:
        return None
    if EMAIL_RE.search(text):
        return None
    if text.lower().startswith(("http://", "https://", "www.")):
        return None
    return text[:255]


def _clean_name(value: str) -> str | None:
    text = re.sub(r"\s+", " ", value.strip())
    text = text.strip(".,;:-")
    if not _looks_like_name(text):
        return None
    return text[:255]


def extract_social_urls(text: str) -> dict[str, str | list[str]]:
    """Collect public social profile URLs from page text."""
    found: dict[str, str | list[str]] = {}
    other: list[str] = []
    for platform, pattern in SOCIAL_URL_PATTERNS.items():
        for match in pattern.findall(text):
            url = match.rstrip(").,;]")
            if platform not in found:
                found[platform] = url
    return found


def _name_before_email(text: str, email: str, email_start: int) -> str | None:
    line_start = text.rfind("\n", 0, email_start) + 1
    prefix_on_line = text[line_start:email_start].strip()
    if prefix_on_line:
        return None

    before = text[max(0, email_start - 120) : email_start]
    lines = [line.strip() for line in before.splitlines() if line.strip()]
    if not lines:
        return None
    candidate = lines[-1]
    if candidate.endswith(":"):
        candidate = candidate[:-1].strip()
    return _clean_name(candidate)


def _socials_near_email(text: str, email: str, email_start: int) -> dict[str, str]:
    window_start = max(0, email_start - 400)
    window_end = min(len(text), email_start + len(email) + 400)
    window = text[window_start:window_end]
    socials = extract_social_urls(window)
    return {key: value for key, value in socials.items() if isinstance(value, str)}


def extract_contacts(
    *,
    markdown: str | None = None,
    html: str | None = None,
) -> list[ExtractedContact]:
    """Extract email contacts and optional names from page content."""
    parts: list[str] = []
    if markdown:
        parts.append(markdown)
    if html:
        parts.append(html)
    if not parts:
        return []

    combined = "\n".join(parts)
    page_socials = extract_social_urls(combined)
    contacts: dict[str, ExtractedContact] = {}

    for match in NAME_EMAIL_ANGLE_RE.finditer(combined):
        name = _clean_name(match.group(1))
        email = normalize_email(match.group(2))
        if is_skipped_email(email):
            continue
        contacts[email] = ExtractedContact(email=email, name=name)

    for match in MAILTO_MD_RE.finditer(combined):
        name = _clean_mailto_name(match.group(1))
        email = normalize_email(match.group(2))
        if is_skipped_email(email):
            continue
        existing = contacts.get(email)
        if existing is None:
            contacts[email] = ExtractedContact(email=email, name=name)
        elif existing.name is None and name is not None:
            existing.name = name

    for match in MAILTO_HTML_RE.finditer(combined):
        email = normalize_email(match.group(1))
        name = _clean_mailto_name(match.group(2))
        if is_skipped_email(email):
            continue
        existing = contacts.get(email)
        if existing is None:
            contacts[email] = ExtractedContact(email=email, name=name)
        elif existing.name is None and name is not None:
            existing.name = name

    for match in EMAIL_RE.finditer(combined):
        email = normalize_email(match.group(0))
        if is_skipped_email(email):
            continue
        if email in contacts:
            if contacts[email].name is None:
                name = _name_before_email(combined, email, match.start())
                if name is not None:
                    contacts[email].name = name
            continue
        name = _name_before_email(combined, email, match.start())
        contacts[email] = ExtractedContact(email=email, name=name)

    if not contacts:
        return []

    if len(contacts) == 1 and page_socials:
        only = next(iter(contacts.values()))
        only.socials = dict(page_socials)
        return list(contacts.values())

    for contact in contacts.values():
        for match in EMAIL_RE.finditer(combined):
            if normalize_email(match.group(0)) != contact.email:
                continue
            near = _socials_near_email(combined, contact.email, match.start())
            if near:
                contact.socials.update(near)

    return list(contacts.values())
