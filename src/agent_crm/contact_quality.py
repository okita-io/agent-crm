"""Contact-quality filters for extraction, verification, and backfill.

Keeps hunt-sourced contacts that look like real prospects: relevant page
context, non-template social links, and notes free of tracking pixels.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

from .hunt_utils import registrable_domain

EmailQualityFilter = Literal["all", "person", "role"]

# Role / shared inbox local parts beyond generic support handles.
ROLE_INBOX_LOCAL_PARTS: frozenset[str] = frozenset(
    {
        "info",
        "hello",
        "hi",
        "contact",
        "sales",
        "enquiries",
        "inquiries",
        "inquiry",
        "general",
        "office",
        "team",
        "mail",
        "email",
        "press",
        "media",
        "pr",
        "marketing",
        "business",
        "accounts",
        "booking",
        "bookings",
        "orders",
        "order",
        "shop",
        "store",
        "reception",
        "frontdesk",
        "front-desk",
        "concierge",
        "careers",
        "jobs",
        "hr",
        "recruiting",
        "recruitment",
        "talent",
        "news",
        "newsletter",
        "subscribe",
        "unsubscribe",
        "membership",
        "members",
        "community",
        "partners",
        "partnerships",
        "sponsorship",
        "sponsor",
        "advertising",
        "ads",
        "ad",
        "wholesale",
        "returns",
        "refunds",
        "warranty",
        "claims",
        "complaints",
        "complaint",
        "enquiry",
        "enquire",
        "inquire",
        "questions",
        "question",
        "ask",
        "talk",
        "chat",
        "welcome",
        "sayhello",
        "getintouch",
        "reachus",
        "reachout",
        "connect",
        "workwithus",
        "collab",
        "collaborate",
        "studio",
        "agency",
        "company",
        "brand",
        "founders",
        "founder",
        "ceo",
        "owner",
        "owners",
        "management",
        "operations",
        "ops",
        "dev",
        "developers",
        "engineering",
        "tech",
        "it",
        "finance",
        "billing",
        "invoice",
        "invoices",
        "payments",
        "payment",
        "accounting",
        "accounts",
        "account",
        "customersuccess",
        "customer-success",
        "success",
        "onboarding",
        "training",
        "education",
        "school",
        "university",
        "college",
        "library",
        "museum",
        "gallery",
        "events",
        "event",
        "tickets",
        "ticket",
        "reservations",
        "reservation",
        "concierge",
    }
)

_FILENAME_EMAIL_DOMAIN_RE = re.compile(
    r"\.(?:png|jpe?g|gif|svg|webp|ico|bmp|tiff?|pdf|docx?|xlsx?|zip|mp[34]|mov|avi)$",
    re.IGNORECASE,
)

_JUNK_PERSON_NAME_RE = re.compile(
    r"(?:"
    r"screenshot|screen[\s\-_]?shot|"
    r"img[\s\-_]?\d+|^img_\d+|^dsc[\-_]?\d+|^photo[\-_]?\d+|"
    r"\.(?:png|jpe?g|gif|svg|webp|pdf|docx?|xlsx?|zip|mp[34])"
    r")",
    re.IGNORECASE,
)

_JUNK_EMAIL_LOCAL_RE = re.compile(
    r"^(?:"
    r"\d{6,}|"
    r"[a-f0-9]{16,}|"
    r"(?:screenshot|screen[\s\-_]?shot|img[\s\-_]?\d+|image\d*)"
    r")$",
    re.IGNORECASE,
)

_PLACEHOLDER_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "domain.com",
        "example.com",
        "example.org",
        "example.net",
        "email.com",
        "mail.com",
        "yourdomain.com",
        "yourcompany.com",
        "company.com",
        "website.com",
        "site.com",
        "test.com",
        "sample.com",
        "placeholder.com",
        "yoursite.com",
        "mydomain.com",
    }
)

_PLACEHOLDER_EMAIL_LOCALS: frozenset[str] = frozenset(
    {
        "name",
        "firstname",
        "lastname",
        "first",
        "last",
        "yourname",
        "your-name",
        "your.email",
        "you",
        "user",
        "username",
        "someone",
        "somebody",
        "person",
        "email",
        "e-mail",
        "mail",
        "contact",
        "example",
        "test",
        "demo",
        "sample",
        "placeholder",
        "foo",
        "bar",
        "john",
        "jane",
    }
)

# Documentation / sample dummy locals (e.g. MDN nowhere@mozilla.org).
_DUMMY_DOCUMENTATION_LOCAL_PARTS: frozenset[str] = frozenset(
    {
        "nowhere",
        "nobody",
        "noone",
        "no-one",
        "null",
        "nil",
        "na",
        "n/a",
        "invalid",
        "discard",
        "drop",
        "fake",
        "dummy",
        # MDN WebExtension sample add-on IDs — not real people.
        "borderify",
        "beastify",
    }
)

_LOCAL_PART_SEPARATOR_RE = re.compile(r"[._\-]")
_ALPHA_TOKEN_RE = re.compile(r"[a-z]+", re.IGNORECASE)
_PERSON_NAME_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]{0,}")

# Domains that should never count as a useful contact-discovery page.
IRRELEVANT_SOURCE_DOMAIN_FRAGMENTS: tuple[str, ...] = (
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "google-analytics.com",
    "googletagmanager.com",
    "facebook.com/tr",
    "clarity.ms",
    "hotjar.com",
    "segment.io",
    "segment.com",
    "mixpanel.com",
    "adservice.",
    "adsystem.",
    "adnxs.com",
    "taboola.com",
    "outbrain.com",
    "criteo.com",
    "pixel.",
    "beacon.",
    "track.",
    "tracking.",
    "analytics.",
    "metrics.",
    "sentry.io",
    "wixpress.com",
    "cloudflare.com",
    "challenges.cloudflare.com",
)

# Community / directory hosts where a third-party email can still be relevant.
COMMUNITY_PLATFORM_DOMAINS: frozenset[str] = frozenset(
    {
        "reddit.com",
        "old.reddit.com",
        "discord.com",
        "discord.gg",
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "x.com",
        "twitter.com",
        "tiktok.com",
        "youtube.com",
        "substack.com",
        "beehiiv.com",
        "medium.com",
        "patreon.com",
        "ko-fi.com",
        "linktr.ee",
        "carrd.co",
        "wixsite.com",
        "wordpress.com",
        "blogspot.com",
        "tumblr.com",
        "lemmy.world",
        "lemmy.ml",
        "lemmy.ca",
        "lemmy.db",
        "lemmy.nz",
        "lemmy.today",
        "lemmy.zip",
    }
)

# Paths that indicate boilerplate, not a person/contact page.
LEGAL_BOILERPLATE_PATH_FRAGMENTS: tuple[str, ...] = (
    "/privacy",
    "/cookie",
    "/cookies",
    "/terms",
    "/legal",
    "/gdpr",
    "/ccpa",
    "/opt-out",
    "/unsubscribe",
)

CONTACT_CONTEXT_PATH_FRAGMENTS: tuple[str, ...] = (
    "/contact",
    "/about",
    "/team",
    "/staff",
    "/author",
    "/profile",
    "/people",
    "/bio",
    "/press",
    "/media-kit",
    "/creators",
    "/case-study",
    "/case-studies",
    "/portfolio",
    "/work",
    "/projects",
    "/webxr",
    "/webar",
    "/xr",
)

# Social share / intent URLs — not real profile pages.
SHARE_LINK_PATH_FRAGMENTS: tuple[str, ...] = (
    "/sharer",
    "/share?",
    "/share/",
    "/intent/tweet",
    "/intent/post",
    "/sharearticle",
    "sharer.php",
    "/dialog/share",
    "/sharing/share-offsite",
    "/plugins/share",
    "/widgets/share",
)

# Generic platform or support handles — not useful prospects.
GENERIC_SOCIAL_HANDLES: frozenset[str] = frozenset(
    {
        "share",
        "sharer",
        "intent",
        "home",
        "help",
        "support",
        "facebook",
        "twitter",
        "instagram",
        "linkedin",
        "youtube",
        "meta",
        "google",
        "ads",
        "advertising",
        "business",
        "marketing",
        "customerservice",
        "customer-service",
        "helpcenter",
        "help-center",
        "contact",
        "info",
        "admin",
        "official",
        "team",
        "news",
        "media",
        "press",
        "privacy",
        "legal",
        "safety",
        "about",
        "explore",
        "discover",
        "search",
        "login",
        "signup",
        "sign-up",
        "register",
        "settings",
        "account",
        "status",
        "careers",
        "jobs",
        "hiring",
    }
)

# Ad-agency / platform marketing accounts.
AD_FIRM_DOMAIN_FRAGMENTS: tuple[str, ...] = (
    "ogilvy",
    "wpp.com",
    "publicis",
    "dentsu",
    "havas",
    "bbdo",
    "mccann",
    "saatchi",
    "razorfish",
    "digitas",
    "facebook.com/business",
    "business.facebook",
    "ads.twitter",
    "business.instagram",
    "linkedin.com/ad",
    "marketing.",
    "advertising.",
)

GENERIC_SUPPORT_LOCAL_PARTS: frozenset[str] = frozenset(
    {
        "support",
        "help",
        "helpdesk",
        "customerservice",
        "customer-service",
        "customercare",
        "customer-care",
        "service",
        "care",
        "feedback",
        "abuse",
        "billing",
        "webmaster",
        "postmaster",
        "hostmaster",
        "admin",
        "administrator",
        "legal",
        "privacy",
        "compliance",
        "security",
        "noreply",
        "no-reply",
        "donotreply",
        "do-not-reply",
        "mailer-daemon",
        "notifications",
    }
)

ROLE_INBOX_LOCAL_PARTS: frozenset[str] = frozenset(
    {
        "info",
        "hello",
        "contact",
        "inquiries",
        "inquiry",
        "general",
        "sales",
        "team",
        "office",
        "press",
        "media",
        "hr",
        "jobs",
        "careers",
        "recruiting",
        "marketing",
        "partners",
        "partner",
    }
)

_TRACKING_PIXEL_URL_RE = re.compile(
    r"https?://[^\s<>\"')\]]+?"
    r"(?:pixel|beacon|/track(?:ing)?|/open\.gif|/sp\.gif|/imp\.|doubleclick|"
    r"googlesyndication|facebook\.com/tr|/collect\?|/event\?|/r\.gif|"
    r"1x1|/px\.|/b\.gif|/i\.gif|/o\.gif|/t\.gif|/v\.gif|/email-open|"
    r"mailtrack|sendgrid\.net/wf/open|list-manage\.com/track|"
    r"hubspot\.com/__ptq|mailchimp\.com/track|click\.)"
    r"[^\s<>\"')\]]*",
    re.IGNORECASE,
)

_WHITESPACE_RE = re.compile(r"\s+")


def email_registrable_domain(email: str) -> str:
    """Best-effort registrable domain from an email address."""
    _, _, domain = email.strip().lower().partition("@")
    if not domain:
        return ""
    parts = domain.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return domain


def _source_host(url: str) -> str:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().removeprefix("www.")
    if ":" in host:
        host = host.split(":", 1)[0]
    return host


def _path_lower(url: str) -> str:
    return urlparse(url.strip()).path.lower()


def is_blocked_source_domain(url: str) -> bool:
    host = _source_host(url)
    lowered = url.lower()
    if not host:
        return True
    for fragment in IRRELEVANT_SOURCE_DOMAIN_FRAGMENTS:
        if fragment in host or fragment in lowered:
            return True
    return False


def is_community_platform(url: str) -> bool:
    host = _source_host(url)
    if not host:
        return False
    if host.startswith("lemmy.") or ".lemmy." in host:
        return True
    for platform in COMMUNITY_PLATFORM_DOMAINS:
        if host == platform or host.endswith(f".{platform}"):
            return True
    return False


def is_legal_boilerplate_path(url: str) -> bool:
    path = _path_lower(url)
    return any(fragment in path for fragment in LEGAL_BOILERPLATE_PATH_FRAGMENTS)


def has_contact_context_path(url: str) -> bool:
    path = _path_lower(url)
    return any(fragment in path for fragment in CONTACT_CONTEXT_PATH_FRAGMENTS)


def is_relevant_source_url(url: str, email: str) -> bool:
    """Return whether an email found on ``url`` is worth keeping."""
    if not url or not url.strip():
        return False
    if is_blocked_source_domain(url):
        return False
    if is_community_platform(url):
        return True

    email_domain = email_registrable_domain(email)
    source_domain = registrable_domain(url)
    if email_domain and source_domain and (
        email_domain == source_domain or source_domain.endswith(f".{email_domain}")
    ):
        return True
    if has_contact_context_path(url):
        return True
    if is_legal_boilerplate_path(url):
        return False
    # Hunt pages on third-party sites that are not ad/tracking hosts stay relevant.
    return True


def is_generic_support_email(email: str) -> bool:
    local, _, domain = email.strip().lower().partition("@")
    if not local or not domain:
        return True
    base_local = local.split("+", 1)[0]
    if base_local in GENERIC_SUPPORT_LOCAL_PARTS:
        return True
    for part in re.split(r"[._\-]", base_local):
        if part in GENERIC_SUPPORT_LOCAL_PARTS:
            return True
    return False


def is_role_inbox_email(email: str) -> bool:
    """Return True for shared / role inboxes (info@, hello@, support@, etc.)."""
    if is_generic_support_email(email):
        return True
    local, _, domain = email.strip().lower().partition("@")
    if not local or not domain:
        return True
    base_local = local.split("+", 1)[0]
    if base_local in ROLE_INBOX_LOCAL_PARTS:
        return True
    for part in re.split(r"[._\-]", base_local):
        if part in ROLE_INBOX_LOCAL_PARTS:
            return True
    return False


def is_filename_as_email(email: str) -> bool:
    """Return True when the address looks like a filename-as-email (name@x.png)."""
    local, _, domain = email.strip().lower().partition("@")
    if not local or not domain:
        return False
    if _FILENAME_EMAIL_DOMAIN_RE.search(domain):
        return True
    if re.search(r"\.(?:png|jpe?g|gif|svg|webp|ico|bmp)$", local, re.IGNORECASE):
        return True
    return False


def is_junk_person_name(name: str | None) -> bool:
    """Return True when a display name looks like a screenshot or filename."""
    if not name:
        return False
    text = name.strip()
    if not text:
        return False
    if _JUNK_PERSON_NAME_RE.search(text):
        return True
    if re.fullmatch(
        r"[\w.\-]+\.(?:png|jpe?g|gif|svg|webp|pdf|docx?|xlsx?|zip|mp[34])",
        text,
        re.IGNORECASE,
    ):
        return True
    return False


def is_obviously_junk_email(email: str) -> bool:
    """Return True for addresses that should never be stored as contacts."""
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized:
        return True
    if is_dummy_documentation_email(normalized):
        return True
    if is_filename_as_email(normalized):
        return True
    local, _, _domain = normalized.partition("@")
    base_local = local.split("+", 1)[0]
    if _JUNK_EMAIL_LOCAL_RE.fullmatch(base_local):
        return True
    return False


def prepare_contact_for_ingest(
    email: str,
    name: str | None = None,
) -> tuple[str, str | None] | None:
    """Return sanitized contact fields or None when ingest should be skipped."""
    normalized = email.strip().lower()
    if is_obviously_junk_email(normalized):
        return None
    clean_name = name.strip()[:255] if isinstance(name, str) and name.strip() else None
    if is_junk_person_name(clean_name):
        clean_name = None
    return normalized, clean_name


def ingest_needs_immediate_invalid_verification(email: str) -> tuple[bool, list[str]]:
    """Return whether a stored contact should be marked INVALID without DNS."""
    normalized = email.strip().lower()
    if is_role_inbox_email(normalized):
        return True, ["role or shared inbox — not a direct prospect address"]
    if is_placeholder_email(normalized):
        return True, ["placeholder or template email (not a prospect)"]
    if is_filename_as_email(normalized):
        return True, ["filename-as-email (not a prospect)"]
    return False, []


def is_dummy_documentation_email(email: str) -> bool:
    """Return True for documentation/sample dummy locals (nowhere@, nobody@, etc.)."""
    local, _, domain = email.strip().lower().partition("@")
    if not local or not domain:
        return False
    base_local = local.split("+", 1)[0]
    return base_local in _DUMMY_DOCUMENTATION_LOCAL_PARTS


def is_placeholder_email(email: str) -> bool:
    """Return True for template placeholders like name@domain.com."""
    local, _, domain = email.strip().lower().partition("@")
    if not local or not domain:
        return True
    if is_dummy_documentation_email(email):
        return True
    if domain in _PLACEHOLDER_EMAIL_DOMAINS:
        return True
    if local in _PLACEHOLDER_EMAIL_LOCALS and domain in _PLACEHOLDER_EMAIL_DOMAINS:
        return True
    if local in _PLACEHOLDER_EMAIL_LOCALS and domain.endswith(".example"):
        return True
    if domain.endswith(".example") or domain.endswith(".invalid") or domain.endswith(".test"):
        return True
    return False


def _stored_name_looks_like_person(name: str | None) -> bool:
    if not name:
        return False
    text = name.strip()
    if not text or len(text) > 80:
        return False
    if "@" in text:
        return False
    words = _PERSON_NAME_WORD_RE.findall(text)
    if len(words) < 2:
        return False
    generic = {
        "contact",
        "email",
        "support",
        "team",
        "sales",
        "info",
        "hello",
        "general",
        "inquiries",
        "inquiry",
        "mail",
        "message",
        "reach",
        "write",
        "send",
        "us",
        "our",
        "the",
        "for",
        "at",
        "or",
        "and",
    }
    meaningful = [word for word in words if word.lower() not in generic]
    return len(meaningful) >= 2


def local_part_has_person_signals(email: str) -> bool:
    """Return True when the local part looks like an individual (jane.doe, jane_doe)."""
    local, _, domain = email.strip().lower().partition("@")
    if not local or not domain:
        return False
    base_local = local.split("+", 1)[0]
    if _LOCAL_PART_SEPARATOR_RE.search(base_local):
        parts = [part for part in re.split(r"[._\-]+", base_local) if part]
        alpha_parts = [part for part in parts if _ALPHA_TOKEN_RE.fullmatch(part)]
        if len(alpha_parts) >= 2:
            return True
    alpha_tokens = _ALPHA_TOKEN_RE.findall(base_local)
    return len(alpha_tokens) >= 2


def is_person_email(
    email: str,
    *,
    name: str | None = None,
    decoded_from_obfuscation: bool = False,
) -> bool:
    """Return True when an email looks like an individual person inbox."""
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized:
        return False
    if is_role_inbox_email(normalized):
        return False
    if is_filename_as_email(normalized):
        return False
    if is_placeholder_email(normalized):
        return False
    if decoded_from_obfuscation:
        return True
    if _stored_name_looks_like_person(name):
        return True
    if local_part_has_person_signals(normalized):
        return True
    return False


def classify_email_quality(
    email: str,
    *,
    name: str | None = None,
    decoded_from_obfuscation: bool = False,
) -> EmailQualityFilter | None:
    """Classify an email as person, role, or None when it is low-quality junk."""
    if is_person_email(
        email,
        name=name,
        decoded_from_obfuscation=decoded_from_obfuscation,
    ):
        return "person"
    if is_role_inbox_email(email):
        return "role"
    if is_filename_as_email(email) or is_placeholder_email(email):
        return None
    return None


def profile_matches_quality_filter(
    email: str,
    *,
    name: str | None = None,
    quality: EmailQualityFilter = "all",
    decoded_from_obfuscation: bool = False,
) -> bool:
    """Return whether a stored profile matches the requested quality filter."""
    if quality == "all":
        return True
    classification = classify_email_quality(
        email,
        name=name,
        decoded_from_obfuscation=decoded_from_obfuscation,
    )
    if quality == "person":
        return classification == "person"
    if quality == "role":
        return classification == "role"
    return True


def is_relevant_contact(email: str, source_urls: list[str] | None) -> bool:
    """Decide whether a contact should be kept as a useful prospect."""
    if is_generic_support_email(email):
        return False
    urls = [url for url in (source_urls or []) if isinstance(url, str) and url.strip()]
    if not urls:
        return False
    return any(is_relevant_source_url(url, email) for url in urls)


def is_share_link_social_url(url: str) -> bool:
    lowered = url.lower()
    return any(fragment in lowered for fragment in SHARE_LINK_PATH_FRAGMENTS)


def _social_handle(platform: str, url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.strip("/")
    if not path:
        return ""
    if platform == "linkedin" and path.lower().startswith("in/"):
        return path.split("/", 1)[1].split("/")[0].lower()
    return path.split("/")[0].lower()


def is_ad_firm_social_url(url: str) -> bool:
    lowered = url.lower()
    return any(fragment in lowered for fragment in AD_FIRM_DOMAIN_FRAGMENTS)


def is_low_quality_social_url(url: str, *, email: str | None = None) -> bool:
    if not url or not url.strip():
        return True
    if is_share_link_social_url(url):
        return True
    if is_ad_firm_social_url(url):
        return True
    platform = None
    host = _source_host(url)
    if host in {"x.com", "twitter.com"}:
        platform = "x"
    elif host == "linkedin.com":
        platform = "linkedin"
    elif host == "instagram.com":
        platform = "instagram"
    elif host == "facebook.com":
        platform = "facebook"
    if platform:
        handle = _social_handle(platform, url)
        if handle in GENERIC_SOCIAL_HANDLES:
            return True
        if email:
            local = email.split("@", 1)[0].lower()
            normalized_handle = handle.replace("-", "").replace("_", "")
            normalized_local = local.replace(".", "").replace("_", "").replace("-", "")
            if normalized_handle and normalized_handle == normalized_local:
                return False
    return False


def filter_socials(
    socials: dict | None,
    *,
    email: str | None = None,
) -> dict | None:
    """Drop share links, ad-firm pages, and generic support handles."""
    if not socials:
        return socials
    cleaned: dict[str, str | list[str]] = {}
    for key, value in socials.items():
        if key == "other":
            if isinstance(value, list):
                kept = [
                    item
                    for item in value
                    if isinstance(item, str) and not is_low_quality_social_url(item, email=email)
                ]
                if kept:
                    cleaned["other"] = kept
            continue
        if isinstance(value, str) and not is_low_quality_social_url(value, email=email):
            cleaned[key] = value
    return cleaned or None


def scrub_tracking_pixel_urls(text: str) -> str:
    """Remove tracking-pixel and open-beacon URLs from free text."""
    if not text:
        return text
    scrubbed = _TRACKING_PIXEL_URL_RE.sub("", text)
    scrubbed = _WHITESPACE_RE.sub(" ", scrubbed).strip()
    return scrubbed


def scrub_notes_value(notes: str | None) -> str | None:
    """Scrub tracking pixels from hunt-resource notes or plain snippets."""
    if not notes:
        return notes
    text = notes.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return scrub_tracking_pixel_urls(text) or None
        if isinstance(payload, dict):
            changed = False
            for key in ("snippet", "label"):
                value = payload.get(key)
                if isinstance(value, str):
                    cleaned = scrub_tracking_pixel_urls(value)
                    if cleaned != value:
                        payload[key] = cleaned
                        changed = True
            if changed:
                return json.dumps(payload, separators=(",", ":"))
            return text
    cleaned = scrub_tracking_pixel_urls(text)
    return cleaned or None


@dataclass
class ContactQualityCleanup:
    """In-place cleanup result for one contact profile."""

    email: str
    kept: bool
    removed_source_urls: list[str] = field(default_factory=list)
    stripped_social_keys: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def clean_contact_data(
    *,
    email: str,
    socials: dict | None,
    source_urls: list[str] | None,
) -> tuple[dict | None, list[str], bool, ContactQualityCleanup]:
    """Apply all contact-quality filters to profile fields."""
    cleanup = ContactQualityCleanup(email=email, kept=True)
    urls = [url for url in (source_urls or []) if isinstance(url, str) and url.strip()]
    kept_urls: list[str] = []
    for url in urls:
        if is_relevant_source_url(url, email):
            kept_urls.append(url)
        else:
            cleanup.removed_source_urls.append(url)

    cleaned_socials = filter_socials(socials, email=email)
    if socials and cleaned_socials != socials:
        for key in socials:
            if key not in (cleaned_socials or {}):
                cleanup.stripped_social_keys.append(key)
            elif isinstance(socials.get(key), str) and socials.get(key) != cleaned_socials.get(key):
                cleanup.stripped_social_keys.append(key)

    if is_placeholder_email(email):
        cleanup.kept = False
        cleanup.reasons.append("placeholder or documentation dummy email")
    elif is_role_inbox_email(email):
        cleanup.kept = False
        cleanup.reasons.append("role or shared inbox email")
    elif is_filename_as_email(email):
        cleanup.kept = False
        cleanup.reasons.append("filename-as-email")
    elif is_generic_support_email(email):
        cleanup.kept = False
        cleanup.reasons.append("generic support or role email")
    elif not kept_urls:
        cleanup.kept = False
        cleanup.reasons.append("no relevant source URLs")
    else:
        cleanup.kept = True

    return cleaned_socials, kept_urls, cleanup.kept, cleanup


@dataclass
class ContactBackfillResult:
    profiles_scanned: int = 0
    profiles_updated: int = 0
    profiles_removed: int = 0
    leads_disqualified: int = 0
    resource_notes_scrubbed: int = 0
    details: list[ContactQualityCleanup] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
