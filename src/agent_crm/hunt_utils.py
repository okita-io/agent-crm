"""URL/query normalization and junk filtering for the outbound hunter."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

from agent_crm.enums import HuntPageType, HuntQueryStatus, HuntResourceKind

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

_SOCIAL_PROFILE_HOSTS = {
    "threads.net",
    "mastodon.social",
    "mastodon.world",
    "mas.to",
    "lemmy.ml",
    "lemmy.world",
    "lemmy.ca",
    "kbin.social",
    "poliverso.org",
    "dbzer0.com",
    "itjust.works",
    "dice.camp",
}

ROMANCE_OUTLET_HOSTS: frozenset[str] = frozenset(
    {
        "publishersweekly.com",
        "kirkusreviews.com",
        "bookriot.com",
        "smartbitchestrashybooks.com",
        "shereads.com",
        "frolicmedia.com",
        "bookpage.com",
        "romance.io",
        "allaboutromance.com",
        "dearauthor.com",
    }
)

ASTROLOGY_OUTLET_HOSTS: frozenset[str] = frozenset(
    {
        "theastrologypodcast.com",
        "astrologyhub.com",
        "astrology.com",
        "astro.com",
        "chani.com",
        "costarastrology.com",
        "thepattern.com",
    }
)

_TRUSTED_QUEUE_ORIGINS: frozenset[str] = frozenset(
    {"seed", "seed_pack", "explicit"}
)
# Hunter-generated origin segments. Audience prefixes like ``marketing:`` may
# wrap these (``marketing:community:reddit/foo``) and still need review.
# ``branch:seed`` must review; ``marketing:seed`` must not.
_HUNTER_ORIGIN_KINDS: frozenset[str] = frozenset(
    {"branch", "community", "person", "handle", "engagement", "company"}
)

_AD_PATH_FRAGMENTS = (
    "/advertise",
    "advertise-with",
    "/media-kit",
    "/mediakit",
    "/sponsorship",
    "/advertising",
)

_KIND_HINTS: list[tuple[HuntResourceKind, tuple[str, ...]]] = [
    (HuntResourceKind.NEWSLETTER, ("newsletter", "substack", "beehiiv", "mailing list")),
    (HuntResourceKind.FORUM, ("forum", "discourse", "community thread")),
    (HuntResourceKind.COMMUNITY, ("community", "discord", "slack", "group")),
    (HuntResourceKind.DIRECTORY, ("directory", "list of", "database", "catalog")),
    (HuntResourceKind.LIST, ("best ", "top ", "roundup", "listicle")),
    (HuntResourceKind.SOCIAL, ("reddit", "twitter", "instagram", "tiktok")),
]


def origin_needs_review(origin: str | None) -> bool:
    """True when a hunter/research/engagement-added term should be reviewed first."""
    value = (origin or "").strip().lower()
    if not value:
        return True
    parts = [part for part in value.split(":") if part]
    if any(part in _HUNTER_ORIGIN_KINDS for part in parts):
        return True
    if value in _TRUSTED_QUEUE_ORIGINS:
        return False
    if value.endswith(":seed_pack") or value.endswith(":seed"):
        return False
    if value.startswith("seed"):
        return False
    if value.startswith("venue:"):
        return False
    return True


def query_enqueue_status(origin: str | None) -> HuntQueryStatus:
    """Trusted seeds go straight to PENDING; hunter-added terms wait for review."""
    if origin_needs_review(origin):
        return HuntQueryStatus.PENDING_REVIEW
    return HuntQueryStatus.PENDING


def normalize_query(query: str) -> str:
    """Collapse whitespace and lowercase for deduplication."""
    return " ".join(query.split()).strip().lower()


def classify_page_type(
    url: str,
    title: str | None = None,
    snippet: str | None = None,
) -> HuntPageType:
    """Lightweight outlet vs profile vs docs vs ad-page classifier."""
    from .hunt_relevance import is_obvious_off_topic_url

    lower = url.lower()
    host = registrable_domain(url)
    haystack = " ".join(filter(None, [url, title, snippet])).lower()

    if is_obvious_off_topic_url(url):
        return HuntPageType.DOCS
    if any(fragment in lower for fragment in _AD_PATH_FRAGMENTS):
        return HuntPageType.AD_PAGE
    if "advertise with" in haystack or "media kit" in haystack:
        return HuntPageType.AD_PAGE

    detailed = classify_resource_detailed(url, title, snippet)
    if detailed.kind == HuntResourceKind.COMMUNITY or detailed.platform in {
        "reddit",
        "discord",
        "facebook",
        "lemmy",
        "discourse",
    }:
        return HuntPageType.COMMUNITY
    if host in _SOCIAL_PROFILE_HOSTS or host.endswith(".threads.net"):
        return HuntPageType.SOCIAL_PROFILE
    if any(host.endswith(social) or host == social for social in _SOCIAL_HOSTS):
        if detailed.kind == HuntResourceKind.COMMUNITY:
            return HuntPageType.COMMUNITY
        return HuntPageType.SOCIAL_PROFILE
    if host in ROMANCE_OUTLET_HOSTS or host in ASTROLOGY_OUTLET_HOSTS:
        path = urlparse(url).path.lower()
        if any(token in path for token in ("/article", "/review", "/20", "/blog")):
            return HuntPageType.OUTLET_ARTICLE
        return HuntPageType.OUTLET_SECTION
    return HuntPageType.OTHER


def classify_domain_class(url: str) -> str:
    """Coarse domain class for filtering: noise, aggregator, social, vertical, unverified."""
    from .hunt_relevance import denied_host_reason

    host = registrable_domain(url)
    if denied_host_reason(url):
        return "noise"
    if host in ROMANCE_OUTLET_HOSTS:
        return "romance_media"
    if host in ASTROLOGY_OUTLET_HOSTS:
        return "astrology_media"
    if host in _SOCIAL_HOSTS or host in _SOCIAL_PROFILE_HOSTS:
        return "social"
    if host == "reddit.com":
        return "community"
    return "unverified_vertical"


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


@dataclass(frozen=True)
class ResourceClassification:
    """URL/title classification with optional community metadata."""

    kind: HuntResourceKind
    community_slug: str | None = None
    community_label: str | None = None
    platform: str | None = None


_REDDIT_SUB_RE = re.compile(r"/r/([A-Za-z0-9_]+)", re.IGNORECASE)
_FACEBOOK_GROUP_RE = re.compile(r"/groups/([A-Za-z0-9.\-]+)", re.IGNORECASE)
_DISCORD_INVITE_RE = re.compile(
    r"(?:discord\.gg|discord\.com/invite)/([A-Za-z0-9\-]+)",
    re.IGNORECASE,
)
_GOOGLE_GROUP_RE = re.compile(r"groups\.google\.com/g/([A-Za-z0-9_\-]+)", re.IGNORECASE)
_LEMMY_COMMUNITY_RE = re.compile(r"/c/([A-Za-z0-9_]+)", re.IGNORECASE)
_DISCOURSE_TOPIC_RE = re.compile(r"/t/([A-Za-z0-9\-]+)", re.IGNORECASE)
_MEETUP_GROUP_RE = re.compile(r"meetup\.com/(?:[A-Za-z]{2}-[A-Za-z]{2}/)?([^/?#]+)", re.IGNORECASE)
_SLACK_COMMUNITY_RE = re.compile(
    r"(?:\.slack\.com|slackin\.com|join\.slack\.com)/(?:t/)?([A-Za-z0-9\-_/]+)?",
    re.IGNORECASE,
)


def _label_from_slug(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip()


def _match_platform(url: str, host: str) -> ResourceClassification | None:
    lower = url.lower()

    reddit_match = _REDDIT_SUB_RE.search(url)
    if "reddit.com" in host and reddit_match:
        slug = reddit_match.group(1)
        return ResourceClassification(
            kind=HuntResourceKind.COMMUNITY,
            community_slug=slug,
            community_label=_label_from_slug(slug),
            platform="reddit",
        )

    facebook_match = _FACEBOOK_GROUP_RE.search(url)
    if "facebook.com" in host and facebook_match:
        slug = unquote(facebook_match.group(1))
        return ResourceClassification(
            kind=HuntResourceKind.COMMUNITY,
            community_slug=slug,
            community_label=_label_from_slug(slug),
            platform="facebook",
        )

    discord_match = _DISCORD_INVITE_RE.search(lower)
    if discord_match:
        slug = discord_match.group(1)
        return ResourceClassification(
            kind=HuntResourceKind.COMMUNITY,
            community_slug=slug,
            community_label=slug,
            platform="discord",
        )

    google_match = _GOOGLE_GROUP_RE.search(lower)
    if google_match:
        slug = google_match.group(1)
        return ResourceClassification(
            kind=HuntResourceKind.COMMUNITY,
            community_slug=slug,
            community_label=_label_from_slug(slug),
            platform="google_groups",
        )

    if "groups.google.com" in host:
        return ResourceClassification(
            kind=HuntResourceKind.COMMUNITY,
            platform="google_groups",
        )

    lemmy_match = _LEMMY_COMMUNITY_RE.search(url)
    if "lemmy" in host and lemmy_match:
        slug = lemmy_match.group(1)
        return ResourceClassification(
            kind=HuntResourceKind.COMMUNITY,
            community_slug=slug,
            community_label=_label_from_slug(slug),
            platform="lemmy",
        )

    if host == "lobste.rs" or host.endswith(".lobste.rs"):
        slug = urlparse(url).path.strip("/").split("/")[0] if urlparse(url).path else None
        return ResourceClassification(
            kind=HuntResourceKind.FORUM,
            community_slug=slug,
            community_label=_label_from_slug(slug) if slug else "Lobsters",
            platform="lobsters",
        )

    if "discourse" in host or _DISCOURSE_TOPIC_RE.search(url):
        topic_match = _DISCOURSE_TOPIC_RE.search(url)
        slug = topic_match.group(1) if topic_match else host.split(".")[0]
        return ResourceClassification(
            kind=HuntResourceKind.FORUM,
            community_slug=slug,
            community_label=_label_from_slug(slug) if slug else None,
            platform="discourse",
        )

    meetup_match = _MEETUP_GROUP_RE.search(lower)
    if "meetup.com" in host and meetup_match:
        slug = meetup_match.group(1)
        if slug not in {"find", "events", "topics"}:
            return ResourceClassification(
                kind=HuntResourceKind.COMMUNITY,
                community_slug=slug,
                community_label=_label_from_slug(slug),
                platform="meetup",
            )

    slack_match = _SLACK_COMMUNITY_RE.search(lower)
    if slack_match and ("slack.com" in host or "slackin.com" in host):
        slug = (slack_match.group(1) or host.split(".")[0]).strip("/")
        return ResourceClassification(
            kind=HuntResourceKind.COMMUNITY,
            community_slug=slug,
            community_label=_label_from_slug(slug) if slug else None,
            platform="slack",
        )

    return None


def classify_resource_detailed(
    url: str,
    title: str | None,
    snippet: str | None = None,
) -> ResourceClassification:
    """Heuristic resource kind and community metadata from URL/title/snippet."""
    haystack = " ".join(filter(None, [url, title, snippet])).lower()
    host = registrable_domain(url)

    platform_match = _match_platform(url, host)
    if platform_match is not None:
        return platform_match

    if any(host.endswith(social) or host == social for social in _SOCIAL_HOSTS):
        return ResourceClassification(kind=HuntResourceKind.SOCIAL)

    for kind, hints in _KIND_HINTS:
        if any(hint in haystack for hint in hints):
            return ResourceClassification(kind=kind)

    return ResourceClassification(kind=HuntResourceKind.OTHER)


def format_resource_notes(
    classification: ResourceClassification,
    snippet: str | None = None,
    *,
    engagement: dict | None = None,
    existing: str | None = None,
) -> str | None:
    """Serialize community metadata, engagement signals, and optional snippet."""
    payload: dict = {}
    if existing:
        text = existing.strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                payload = parsed
            else:
                payload["snippet"] = text[:400]
        else:
            payload["snippet"] = text[:400]

    if classification.platform:
        payload["community"] = classification.platform
    if classification.community_slug:
        payload["slug"] = classification.community_slug
    if classification.community_label:
        payload["label"] = classification.community_label
    if snippet:
        payload["snippet"] = snippet[:400]
    if engagement:
        existing_engagement = payload.get("engagement")
        merged = dict(existing_engagement) if isinstance(existing_engagement, dict) else {}
        merged.update(engagement)
        payload["engagement"] = merged

    if payload:
        return json.dumps(payload, separators=(",", ":"))
    return snippet


def classify_resource(url: str, title: str | None, snippet: str | None = None) -> HuntResourceKind:
    """Heuristic resource kind from URL/title/snippet."""
    return classify_resource_detailed(url, title, snippet).kind


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
