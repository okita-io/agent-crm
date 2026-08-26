"""Public people-enrichment for contact profiles (SERP, public pages, Spark)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import get_settings
from .contact_quality import filter_socials, is_role_inbox_email
from .contact_social_lookup import (
    _matches_contact,
    _normalize_profile_url,
    _pick_profile_hit,
    _platform_from_url,
)
from .firecrawl_client import FirecrawlError, scrape
from .llm_client import chat_completions
from .searxng_client import SearchResult, search

logger = logging.getLogger(__name__)

ACTOR = "contact-enrichment"

LOGIN_WALLED_HOSTS: frozenset[str] = frozenset(
    {
        "linkedin.com",
        "www.linkedin.com",
        "facebook.com",
        "www.facebook.com",
        "instagram.com",
        "www.instagram.com",
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
    }
)

COMPANY_PATH_SUFFIXES: tuple[str, ...] = (
    "/team",
    "/about",
    "/people",
    "/contact",
    "/our-team",
    "/leadership",
    "/staff",
)

LINKEDIN_TITLE_RE = re.compile(
    r"^(.+?)\s*[-–|]\s*(.+?)\s*\|\s*LinkedIn\s*$",
    re.IGNORECASE,
)
LINKEDIN_NAME_ONLY_RE = re.compile(r"^(.+?)\s*\|\s*LinkedIn\s*$", re.IGNORECASE)
FACEBOOK_TITLE_RE = re.compile(
    r"^(.+?)\s*[-–|]\s*(.+?)\s*\|\s*Facebook\s*$",
    re.IGNORECASE,
)
X_TITLE_RE = re.compile(
    r"^(.+?)\s*\(@([^)]+)\)\s*/\s*X\s*$",
    re.IGNORECASE,
)
TITLE_AT_ORG_RE = re.compile(r"^(.*?)\s+at\s+(.+)$", re.IGNORECASE)

BAD_NAME_FRAGMENTS: frozenset[str] = frozenset(
    {
        "email us",
        "contact us",
        "get in touch",
        "reach us",
        "our team",
        "support team",
        "general inquiries",
        "file not found",
        "404",
        "index",
        "home",
        "untitled",
    }
)

_FILENAME_RE = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|png|jpe?g|gif|svg|zip)$", re.IGNORECASE)


@dataclass
class SerpEvidence:
    """One SERP hit used as enrichment evidence."""

    url: str
    title: str
    snippet: str
    platform: str | None = None


@dataclass
class PageEvidence:
    """One scraped public page excerpt."""

    url: str
    title: str | None
    excerpt: str


@dataclass
class PeopleEnrichmentFields:
    """Structured people facts extracted from public sources."""

    name: str | None = None
    title: str | None = None
    organization: str | None = None
    location: str | None = None
    bio: str | None = None
    socials: dict[str, str] = field(default_factory=dict)


@dataclass
class PeopleEnrichmentResult:
    """Full enrichment output including evidence metadata."""

    fields: PeopleEnrichmentFields
    serp_evidence: list[SerpEvidence] = field(default_factory=list)
    page_evidence: list[PageEvidence] = field(default_factory=list)
    queries_used: int = 0
    spark_used: bool = False
    pages_scraped: int = 0


def is_login_walled_url(url: str) -> bool:
    host = urlparse(url.strip()).netloc.lower().removeprefix("www.")
    return host in {h.removeprefix("www.") for h in LOGIN_WALLED_HOSTS}


def _clean_person_name(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value.strip())
    if not cleaned or len(cleaned) < 2:
        return None
    lowered = cleaned.lower()
    if lowered in BAD_NAME_FRAGMENTS:
        return None
    if _FILENAME_RE.search(lowered):
        return None
    if lowered.endswith(".com") or lowered.endswith(".org"):
        return None
    # Reject names that look like sentences
    if len(cleaned.split()) > 5:
        return None
    return cleaned


def _clean_field(value: str | None, max_len: int = 255) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value.strip())
    if not cleaned:
        return None
    return cleaned[:max_len]


def _parse_title_at_org(segment: str) -> tuple[str | None, str | None]:
    segment = segment.strip()
    if not segment:
        return None, None
    match = TITLE_AT_ORG_RE.match(segment)
    if match:
        return _clean_field(match.group(1)), _clean_field(match.group(2))
    if "|" in segment:
        parts = [part.strip() for part in segment.split("|") if part.strip()]
        if len(parts) >= 2:
            return _clean_field(parts[0]), _clean_field(parts[-1])
    return _clean_field(segment), None


def parse_linkedin_serp_title(title: str) -> PeopleEnrichmentFields:
    """Parse LinkedIn SERP titles like 'Jane Doe - VP Marketing at Acme | LinkedIn'."""
    fields = PeopleEnrichmentFields()
    match = LINKEDIN_TITLE_RE.match(title.strip())
    if match:
        name = _clean_person_name(match.group(1))
        title_part, org = _parse_title_at_org(match.group(2))
        fields.name = name
        fields.title = title_part
        fields.organization = org
        return fields

    name_match = LINKEDIN_NAME_ONLY_RE.match(title.strip())
    if name_match:
        fields.name = _clean_person_name(name_match.group(1))
    return fields


def parse_facebook_serp_title(title: str) -> PeopleEnrichmentFields:
    """Parse Facebook SERP titles."""
    fields = PeopleEnrichmentFields()
    match = FACEBOOK_TITLE_RE.match(title.strip())
    if not match:
        return fields
    fields.name = _clean_person_name(match.group(1))
    second = match.group(2).strip()
    if second and second.lower() not in {"facebook", "profile"}:
        title_part, org = _parse_title_at_org(second)
        fields.title = title_part
        fields.organization = org
    return fields


def parse_x_serp_title(title: str) -> PeopleEnrichmentFields:
    """Parse X SERP titles like 'Jane Doe (@janedoe) / X'."""
    fields = PeopleEnrichmentFields()
    match = X_TITLE_RE.match(title.strip())
    if match:
        fields.name = _clean_person_name(match.group(1))
    return fields


def parse_serp_hit(hit: SearchResult, *, email: str, name: str | None) -> PeopleEnrichmentFields:
    """Parse a SERP hit title/snippet into people fields when possible."""
    platform = _platform_from_url(hit.url)
    parsed = PeopleEnrichmentFields()
    if platform == "linkedin":
        parsed = parse_linkedin_serp_title(hit.title)
    elif platform == "facebook":
        parsed = parse_facebook_serp_title(hit.title)
    elif platform == "x":
        parsed = parse_x_serp_title(hit.title)

    if not parsed.name and name:
        haystack = f"{hit.title} {hit.snippet}".lower()
        if name.lower() in haystack:
            parsed.name = _clean_person_name(name)

    if platform and _matches_contact(
        email=email,
        name=name or parsed.name,
        url=hit.url,
        title=hit.title,
        snippet=hit.snippet,
    ):
        parsed.socials[platform] = _normalize_profile_url(hit.url)

    return parsed


def _merge_fields(
    base: PeopleEnrichmentFields,
    incoming: PeopleEnrichmentFields,
) -> PeopleEnrichmentFields:
    if incoming.name and not base.name:
        base.name = incoming.name
    if incoming.title and not base.title:
        base.title = incoming.title
    if incoming.organization and not base.organization:
        base.organization = incoming.organization
    if incoming.location and not base.location:
        base.location = incoming.location
    if incoming.bio and not base.bio:
        base.bio = incoming.bio
    for key, value in incoming.socials.items():
        if key not in base.socials:
            base.socials[key] = value
    return base


def serp_fields_sufficient(fields: PeopleEnrichmentFields) -> bool:
    """True when SERP parsing already yields name plus workplace signals."""
    has_name = bool(fields.name)
    has_work = bool(fields.title and fields.organization)
    return has_name and has_work


def build_enrichment_queries(email: str, name: str | None) -> list[str]:
    """Build a bounded SERP query pack for people enrichment."""
    settings = get_settings()
    cap = settings.contact_enrichment_queries_per_profile
    local, _, domain = email.lower().partition("@")
    queries: list[str] = [f'"{email}"']

    if name:
        queries.append(f'"{name}" site:linkedin.com/in')
        queries.append(f'"{name}" site:facebook.com')
        queries.append(f'"{name}" site:x.com OR site:twitter.com')
    else:
        queries.append(f'"{email}" site:linkedin.com/in')
        queries.append(f'"{email}" site:facebook.com')

    if domain:
        domain_token = domain.split(".", 1)[0]
        if name:
            queries.append(f'"{name}" {domain}')
        queries.append(f"{local} {domain}")

    return queries[:cap]


def _company_page_urls(email: str) -> list[str]:
    _, _, domain = email.lower().partition("@")
    if not domain or domain.count(".") < 1:
        return []
    base = f"https://{domain}"
    return [f"{base}{suffix}" for suffix in COMPANY_PATH_SUFFIXES]


def collect_serp_evidence(
    *,
    email: str,
    name: str | None,
    client: httpx.Client | None = None,
    max_queries: int | None = None,
) -> tuple[list[SerpEvidence], PeopleEnrichmentFields, int]:
    """Run SERP searches and parse public profile titles/snippets."""
    settings = get_settings()
    query_cap = (
        max_queries
        if max_queries is not None
        else settings.contact_enrichment_queries_per_profile
    )
    queries = build_enrichment_queries(email, name)[:query_cap]

    evidence: list[SerpEvidence] = []
    merged = PeopleEnrichmentFields()
    queries_used = 0

    for query in queries:
        results = search(query, limit=8, client=client)
        queries_used += 1
        for hit in results:
            platform = _platform_from_url(hit.url)
            if platform:
                picked = _pick_profile_hit(
                    [hit],
                    platform=platform,
                    email=email,
                    name=name or merged.name,
                )
                if picked:
                    merged.socials.setdefault(platform, picked)

            parsed = parse_serp_hit(hit, email=email, name=name or merged.name)
            merged = _merge_fields(merged, parsed)
            evidence.append(
                SerpEvidence(
                    url=hit.url,
                    title=hit.title,
                    snippet=hit.snippet,
                    platform=platform,
                )
            )

    merged.socials = filter_socials(merged.socials, email=email) or {}
    return evidence, merged, queries_used


def collect_page_evidence(
    *,
    email: str,
    name: str | None,
    serp_evidence: list[SerpEvidence],
    firecrawl_client: httpx.Client | None = None,
    max_pages: int = 2,
) -> tuple[list[PageEvidence], PeopleEnrichmentFields]:
    """Scrape public company/news pages via Firecrawl (never login-walled hosts)."""
    urls: list[str] = []
    for hit in serp_evidence:
        if not is_login_walled_url(hit.url) and hit.url not in urls:
            urls.append(hit.url)
    for url in _company_page_urls(email):
        if url not in urls:
            urls.append(url)

    evidence: list[PageEvidence] = []
    merged = PeopleEnrichmentFields()
    scraped = 0

    for url in urls:
        if scraped >= max_pages:
            break
        if is_login_walled_url(url):
            continue
        try:
            page = scrape(url, client=firecrawl_client)
        except FirecrawlError:
            logger.debug("Firecrawl skipped %s", url)
            continue

        excerpt = (page.markdown or "").strip()[:2500]
        if not excerpt:
            continue
        scraped += 1
        evidence.append(PageEvidence(url=url, title=page.title, excerpt=excerpt))

        haystack = excerpt.lower()
        if name and name.lower() in haystack:
            merged.name = _clean_person_name(name)
        if email.lower() in haystack:
            local = email.split("@", 1)[0]
            if name and local.lower() in haystack:
                merged.name = _clean_person_name(name)

    return evidence, merged


def _evidence_block(
    serp_evidence: list[SerpEvidence],
    page_evidence: list[PageEvidence],
) -> str:
    blocks: list[str] = []
    for item in serp_evidence[:12]:
        blocks.append(
            f"SERP {item.url}\nTitle: {item.title}\nSnippet: {item.snippet}"
        )
    for item in page_evidence[:4]:
        blocks.append(
            f"PAGE {item.url}\nTitle: {item.title or ''}\nExcerpt: {item.excerpt[:1200]}"
        )
    return "\n\n".join(blocks)


def _extract_chat_json(response: dict[str, Any]) -> dict[str, Any] | None:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def extract_with_spark(
    *,
    email: str,
    name: str | None,
    serp_evidence: list[SerpEvidence],
    page_evidence: list[PageEvidence],
    existing: PeopleEnrichmentFields,
) -> PeopleEnrichmentFields:
    """Use Spark (via queue) to extract structured fields from collected evidence."""
    evidence_text = _evidence_block(serp_evidence, page_evidence)
    if not evidence_text.strip():
        return existing

    prompt = (
        "Extract person facts for this email contact from the evidence below. "
        "Return JSON with keys: name, title, organization, location, bio. "
        "Use null for any field you cannot support from the evidence. "
        "Do not invent facts. bio should be one short sentence max.\n\n"
        f"Email: {email}\n"
        f"Known name hint: {name or 'unknown'}\n\n"
        f"Evidence:\n{evidence_text}"
    )
    try:
        response = chat_completions(
            {
                "model": "crm",
                "messages": [
                    {
                        "role": "system",
                        "content": "You extract structured contact facts from public web evidence. "
                        "Respond with JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 300,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=120.0,
            actor=ACTOR,
            task=f"enrich {email}",
        )
    except Exception:  # noqa: BLE001
        logger.exception("Spark enrichment failed for %s", email)
        return existing

    payload = _extract_chat_json(response)
    if not payload:
        return existing

    spark_fields = PeopleEnrichmentFields(
        name=_clean_person_name(payload.get("name") if isinstance(payload.get("name"), str) else None),
        title=_clean_field(payload.get("title") if isinstance(payload.get("title"), str) else None),
        organization=_clean_field(
            payload.get("organization") if isinstance(payload.get("organization"), str) else None
        ),
        location=_clean_field(payload.get("location") if isinstance(payload.get("location"), str) else None),
        bio=_clean_field(
            payload.get("bio") if isinstance(payload.get("bio"), str) else None,
            max_len=500,
        ),
    )
    return _merge_fields(existing, spark_fields)


def enrich_contact_person(
    *,
    email: str,
    name: str | None,
    searx_client: httpx.Client | None = None,
    firecrawl_client: httpx.Client | None = None,
    allow_spark: bool = True,
    max_pages: int = 2,
) -> PeopleEnrichmentResult | None:
    """Run public people-enrichment for one contact email.

    Returns None when the email is a role inbox or enrichment is ineligible.
    """
    if is_role_inbox_email(email):
        return None

    serp_evidence, serp_fields, queries_used = collect_serp_evidence(
        email=email,
        name=name,
        client=searx_client,
    )

    page_evidence: list[PageEvidence] = []
    page_fields = PeopleEnrichmentFields()
    pages_scraped = 0

    if not serp_fields_sufficient(serp_fields):
        page_evidence, page_fields = collect_page_evidence(
            email=email,
            name=name or serp_fields.name,
            serp_evidence=serp_evidence,
            firecrawl_client=firecrawl_client,
            max_pages=max_pages,
        )
        pages_scraped = len(page_evidence)

    merged = _merge_fields(serp_fields, page_fields)
    spark_used = False

    if allow_spark and not serp_fields_sufficient(merged):
        merged = extract_with_spark(
            email=email,
            name=name or merged.name,
            serp_evidence=serp_evidence,
            page_evidence=page_evidence,
            existing=merged,
        )
        spark_used = True

    if merged.socials:
        cleaned = filter_socials(merged.socials, email=email)
        merged.socials = cleaned or {}

    return PeopleEnrichmentResult(
        fields=merged,
        serp_evidence=serp_evidence,
        page_evidence=page_evidence,
        queries_used=queries_used,
        spark_used=spark_used,
        pages_scraped=pages_scraped,
    )


def build_enrichment_metadata(result: PeopleEnrichmentResult) -> dict[str, Any]:
    """Serialize enrichment evidence for the contact_profiles.enrichment column."""
    return {
        "enriched_at": datetime.now(UTC).isoformat(),
        "queries_used": result.queries_used,
        "spark_used": result.spark_used,
        "pages_scraped": result.pages_scraped,
        "sources": [
            {
                "type": "serp",
                "url": item.url,
                "title": item.title,
                "snippet": item.snippet,
                "platform": item.platform,
            }
            for item in result.serp_evidence[:20]
        ]
        + [
            {
                "type": "page",
                "url": item.url,
                "title": item.title,
                "excerpt": item.excerpt[:500],
            }
            for item in result.page_evidence[:10]
        ],
    }
