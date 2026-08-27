"""Deterministic contact extraction from scraped page markdown and HTML."""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import unquote

from .contact_quality import (
    filter_socials,
    is_dummy_documentation_email,
    is_filename_as_email,
    is_junk_person_name,
    is_low_quality_social_url,
    is_role_inbox_email,
)

if TYPE_CHECKING:
    from .contact_store import ContactExtractionBudget

logger = logging.getLogger(__name__)

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

_AT_TOKEN_RE = re.compile(
    r"(?:\[at\]|\(at\)|\bat\b|&#64;|%40|@)",
    re.IGNORECASE,
)

# first last dot company dot com -> first.last@company.com
_SPACE_NAME_DOT_DOMAIN_RE = re.compile(
    r"(?P<local>(?:[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,4}))"
    r"\s+dot\s+"
    r"(?P<domain>(?:[A-Za-z][A-Za-z0-9\-]*(?:\s+dot\s+[A-Za-z][A-Za-z0-9\-]*)+))",
    re.IGNORECASE,
)

# jane doe at acme.com -> jane.doe@acme.com
_SPACE_NAME_AT_DOMAIN_RE = re.compile(
    r"(?P<local>(?:[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){1,4}))"
    r"\s+at\s+"
    r"(?P<domain>[A-Za-z0-9][A-Za-z0-9.\-]*\.[A-Za-z]{2,})",
    re.IGNORECASE,
)

_OBFUSCATION_CANDIDATE_RE = re.compile(
    r"(?:"
    r"\b(?:at|dot)\b|"
    r"\[at\]|\[dot\]|\(at\)|\(dot\)|"
    r"&#64;|&#46;|%40|%2e|"
    r"\bemail\b|\breach\b|\bcontact\b"
    r")",
    re.IGNORECASE,
)

_OBFUSCATION_SPAN_RE = re.compile(
    r"(?:"
    r"[A-Za-z0-9][A-Za-z0-9'\-]*(?:\s+[A-Za-z0-9][A-Za-z0-9'\-]*){0,6}\s+"
    r"(?:at|\[at\]|\(at\)|&#64;|%40|@)\s+"
    r"[A-Za-z0-9][A-Za-z0-9.\-]*(?:\s+(?:dot|\[dot\]|\(dot\)|&#46;|%2e)\s+[A-Za-z0-9][A-Za-z0-9.\-]*)+"
    r"|"
    r"[A-Za-z0-9._%+\-]+\s*(?:@|at)\s*[A-Za-z0-9][A-Za-z0-9.\-]*(?:\s+dot\s+[A-Za-z0-9][A-Za-z0-9.\-]*)+"
    r"|"
    r"[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){1,4}\s+dot\s+"
    r"[A-Za-z][A-Za-z0-9\-]*(?:\s+dot\s+[A-Za-z][A-Za-z0-9\-]*)+"
    r")",
    re.IGNORECASE,
)

# Image filenames / asset paths where ``at`` must not become ``@``.
_ASSET_FILENAME_SPAN_RE = re.compile(
    r"(?<![A-Za-z0-9@])"
    r"(?:"
    r"(?:screenshot|screen[\s\-_]?shot|cleanshot|clean[\s\-_]?shot|"
    r"whatsapp[\s\-_]?image|untitled)[\w.\-]*"
    r"(?:[\s\-]+(?:at|@)[\s\-]*[\d.:apm\-]+)?"
    r"(?:@(?:\d+x(?:\.[a-f0-9]+)?))?"
    r"\.(?:png|jpe?g|gif|webp|svg|bmp|tiff?)"
    r"|"
    r"[\w.\-]+(?:[\s\-]+(?:at|@)[\s\-]*[\d.:apm\-]+(?:[\s\-]*[ap]m)?)?"
    r"(?:@(?:\d+x(?:\.[a-f0-9]+)?))?"
    r"\.(?:png|jpe?g|gif|webp|svg|bmp|tiff?)"
    r"|"
    r"[\w.\-]+@(?:\d+x(?:\.[a-f0-9]+)?)\.(?:png|jpe?g|gif|webp|svg)"
    r")"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)

SPARK_DECODE_ACTOR = "contact-extractor"


@dataclass
class ExtractedContact:
    """One email contact found on a page."""

    email: str
    name: str | None = None
    socials: dict[str, str | list[str]] = field(default_factory=dict)
    decoded_from_obfuscation: bool = False


def normalize_email(email: str) -> str:
    """Normalize a single addr-spec; strip mailto/query garbage before storage."""
    text = unquote(email.strip())
    lowered = text.lower()
    if lowered.startswith("mailto:"):
        text = text[7:]
    # mailto:?cc=, &subject=, &body= query strings are not part of the address.
    text = text.split("?", 1)[0].split("#", 1)[0].strip()
    if "," in text:
        text = text.split(",", 1)[0].strip()
    return text.lower().rstrip(".,;)")


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
    if is_dummy_documentation_email(normalized):
        return True
    if is_filename_as_email(normalized):
        return True
    return False


def _mask_asset_filename_spans(text: str) -> str:
    """Blank out image/asset filename spans before at→@ obfuscation decoding."""

    def _blank(match: re.Match[str]) -> str:
        return " " * len(match.group(0))

    return _ASSET_FILENAME_SPAN_RE.sub(_blank, text)


def _normalize_obfuscation_entities(text: str) -> str:
    decoded = html.unescape(text)
    return unquote(decoded)


def _spaces_to_dots(value: str) -> str:
    return ".".join(part for part in value.strip().split() if part)


def _local_from_spaced_name(value: str) -> str:
    return _spaces_to_dots(value.strip())


def _domain_from_spaced_dots(value: str) -> str:
    parts = [part.strip() for part in re.split(r"\s+dot\s+", value, flags=re.IGNORECASE) if part.strip()]
    return ".".join(parts)


def _replace_at_dot_tokens(text: str) -> str:
    text = re.sub(
        r"\s*(?:\[at\]|\(at\)|\bat\b|&#64;|%40|@)\s*",
        "@",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s*(?:\[dot\]|\(dot\)|\bdot\b|&#46;|%2e)\s*",
        ".",
        text,
        flags=re.IGNORECASE,
    )
    return text


def decode_obfuscated_email_deterministic(text: str) -> list[tuple[str, str | None]]:
    """Decode common anti-scraper email obfuscations without Spark."""
    if not text or not text.strip():
        return []

    normalized = _normalize_obfuscation_entities(text)
    found: list[tuple[str, str | None]] = []
    seen: set[str] = set()

    def add_email(raw_email: str, name: str | None = None) -> None:
        email = normalize_email(raw_email)
        if is_skipped_email(email) or email in seen:
            return
        seen.add(email)
        cleaned_name = _clean_name(name) if name else None
        found.append((email, cleaned_name))

    for match in _SPACE_NAME_AT_DOMAIN_RE.finditer(normalized):
        local = _local_from_spaced_name(match.group("local"))
        domain = match.group("domain").strip().lower()
        add_email(f"{local}@{domain}", match.group("local"))

    for match in _SPACE_NAME_DOT_DOMAIN_RE.finditer(normalized):
        local = _local_from_spaced_name(match.group("local"))
        domain = _domain_from_spaced_dots(match.group("domain"))
        add_email(f"{local}@{domain}", match.group("local"))

    tokenized = _replace_at_dot_tokens(_mask_asset_filename_spans(normalized))
    for match in EMAIL_RE.finditer(tokenized):
        prefix = tokenized[max(0, match.start() - 40) : match.start()]
        if re.search(r"[A-Za-z]\s+$", prefix):
            continue
        add_email(match.group(0))

    return found


def _looks_like_obfuscated_candidate(text: str) -> bool:
    if not text or not _OBFUSCATION_CANDIDATE_RE.search(text):
        return False
    if EMAIL_RE.search(text):
        return False
    return bool(_OBFUSCATION_SPAN_RE.search(text))


def _collect_obfuscation_spans(text: str, *, max_spans: int = 6) -> list[str]:
    spans: list[str] = []
    seen: set[str] = set()
    for match in _OBFUSCATION_SPAN_RE.finditer(text):
        span = match.group(0).strip()
        key = span.lower()
        if key in seen:
            continue
        seen.add(key)
        spans.append(span[:400])
        if len(spans) >= max_spans:
            break
    if spans:
        return spans
    if _looks_like_obfuscated_candidate(text):
        for line in text.splitlines():
            if _looks_like_obfuscated_candidate(line):
                snippet = line.strip()[:400]
                if snippet:
                    spans.append(snippet)
                if len(spans) >= max_spans:
                    break
    return spans


def _parse_spark_email_json(content: str) -> list[dict[str, str | None]]:
    from .llm_text import extract_json_object

    payload = extract_json_object(content)
    if not payload:
        return []
    emails = payload.get("emails")
    if not isinstance(emails, list):
        return []
    parsed: list[dict[str, str | None]] = []
    for item in emails:
        if isinstance(item, str):
            parsed.append({"email": item, "name": None})
        elif isinstance(item, dict) and isinstance(item.get("email"), str):
            name = item.get("name")
            parsed.append(
                {
                    "email": item["email"],
                    "name": name if isinstance(name, str) else None,
                }
            )
    return parsed


def decode_obfuscated_emails_spark(
    text: str,
    *,
    budget: ContactExtractionBudget | None = None,
    max_spans: int = 4,
) -> list[tuple[str, str | None]]:
    """Use Spark (via spark-queue) to decode leftover obfuscated email candidates."""
    if budget is None or not budget.consume_spark_decode():
        return []

    spans = _collect_obfuscation_spans(text, max_spans=max_spans)
    if not spans:
        return []

    from .llm_client import chat_completions
    from .llm_text import UNTRUSTED_DATA_SYSTEM_SUFFIX, wrap_untrusted

    snippet_block = "\n---\n".join(
        wrap_untrusted(f"span_{idx}", span, max_chars=400)
        for idx, span in enumerate(spans, start=1)
    )
    prompt = (
        "Extract zero or more real email addresses from the obfuscated snippets below. "
        "Decode anti-scraper forms like 'jane at acme dot com'. "
        "Do NOT invent addresses. Skip role/shared inboxes (info@, hello@, support@). "
        "Respond with JSON only: "
        '{"emails":[{"email":"jane@acme.com","name":"Jane Doe"}]}\n\n'
        f"Snippets:\n{snippet_block}"
    )
    try:
        response = chat_completions(
            {
                "model": "crm",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You extract emails from obfuscated text. Output JSON only."
                            + UNTRUSTED_DATA_SYSTEM_SUFFIX
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 300,
            },
            timeout=90.0,
            actor=SPARK_DECODE_ACTOR,
            task="decode obfuscated emails",
        )
        content = response["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        logger.exception("Spark obfuscation decode failed")
        return []

    found: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for item in _parse_spark_email_json(content):
        email = normalize_email(item["email"] or "")
        if not email or is_skipped_email(email) or is_role_inbox_email(email):
            continue
        if email in seen:
            continue
        seen.add(email)
        name = _clean_name(item["name"] or "") if item.get("name") else None
        found.append((email, name))
    return found


def _looks_like_name(value: str) -> bool:
    text = value.strip()
    if not text or "@" in text or len(text) > 80:
        return False
    if is_junk_person_name(text):
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
    if is_junk_person_name(text):
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


def _name_from_local_part(email: str) -> str | None:
    local = email.split("@", 1)[0]
    if _AT_TOKEN_RE.search(local) or "." not in local:
        return None
    parts = [part for part in re.split(r"[._\-]+", local) if part and part.isalpha()]
    if len(parts) < 2:
        return None
    candidate = " ".join(part.capitalize() for part in parts[:3])
    return _clean_name(candidate)


def extract_social_urls(text: str, *, email: str | None = None) -> dict[str, str | list[str]]:
    """Collect public social profile URLs from page text."""
    found: dict[str, str | list[str]] = {}
    for platform, pattern in SOCIAL_URL_PATTERNS.items():
        for match in pattern.findall(text):
            url = match.rstrip(").,;]")
            if is_low_quality_social_url(url, email=email):
                continue
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
    socials = extract_social_urls(window, email=email)
    return {key: value for key, value in socials.items() if isinstance(value, str)}


def _register_contact(
    contacts: dict[str, ExtractedContact],
    email: str,
    *,
    name: str | None = None,
    decoded_from_obfuscation: bool = False,
) -> None:
    existing = contacts.get(email)
    if existing is None:
        contacts[email] = ExtractedContact(
            email=email,
            name=name,
            decoded_from_obfuscation=decoded_from_obfuscation,
        )
        return
    if existing.name is None and name is not None:
        existing.name = name
    if decoded_from_obfuscation:
        existing.decoded_from_obfuscation = True


def extract_contacts(
    *,
    markdown: str | None = None,
    html: str | None = None,
    budget: ContactExtractionBudget | None = None,
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

    for email, name in decode_obfuscated_email_deterministic(combined):
        if is_skipped_email(email):
            continue
        _register_contact(
            contacts,
            email,
            name=name or _name_from_local_part(email),
            decoded_from_obfuscation=True,
        )

    spark_budget = budget
    if spark_budget is not None and spark_budget.spark_decode_remaining > 0:
        spark_batch = decode_obfuscated_emails_spark(combined, budget=spark_budget)
        for email, name in spark_batch[:spark_budget.spark_decode_per_page]:
            if is_skipped_email(email):
                continue
            _register_contact(
                contacts,
                email,
                name=name or _name_from_local_part(email),
                decoded_from_obfuscation=True,
            )

    for match in NAME_EMAIL_ANGLE_RE.finditer(combined):
        name = _clean_name(match.group(1))
        email = normalize_email(match.group(2))
        if is_skipped_email(email):
            continue
        _register_contact(contacts, email, name=name)

    for match in MAILTO_MD_RE.finditer(combined):
        name = _clean_mailto_name(match.group(1))
        email = normalize_email(match.group(2))
        if is_skipped_email(email):
            continue
        _register_contact(contacts, email, name=name)

    for match in MAILTO_HTML_RE.finditer(combined):
        email = normalize_email(match.group(1))
        name = _clean_mailto_name(match.group(2))
        if is_skipped_email(email):
            continue
        _register_contact(contacts, email, name=name)

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
        _register_contact(contacts, email, name=name)

    if not contacts:
        return []

    if len(contacts) == 1 and page_socials:
        only = next(iter(contacts.values()))
        only.socials = dict(
            filter_socials(page_socials, email=only.email) or {}
        )
        return list(contacts.values())

    for contact in contacts.values():
        for match in EMAIL_RE.finditer(combined):
            if normalize_email(match.group(0)) != contact.email:
                continue
            near = _socials_near_email(combined, contact.email, match.start())
            if near:
                contact.socials.update(near)
        contact.socials = filter_socials(contact.socials, email=contact.email) or {}

    return list(contacts.values())
