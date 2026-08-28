"""Page-signal extraction and SEO issue engine.

Adapted from OpenSEO site-audit issue types. Detection is local (Firecrawl
markdown + metadata + SearXNG). This module never patches a live site.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

from .hunt_utils import canonical_url, registrable_domain

_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
_JSON_LD_HINTS = (
    "application/ld+json",
    '"@type"',
    '"@context": "https://schema.org"',
    '"@context":"https://schema.org"',
)
_WORD_RE = re.compile(r"[A-Za-z0-9']+")

SEVERITY_PENALTY = {"critical": 20, "high": 12, "medium": 6, "low": 3}


@dataclass(frozen=True)
class PageSignals:
    """Technical SEO signals extracted from one scraped page."""

    url: str
    title: str | None = None
    title_length: int = 0
    meta_description: str | None = None
    meta_length: int = 0
    h1_count: int = 0
    h1_text: tuple[str, ...] = ()
    heading_outline: tuple[str, ...] = ()
    word_count: int = 0
    image_count: int = 0
    images_missing_alt: int = 0
    internal_links: int = 0
    external_links: int = 0
    same_domain_urls: tuple[str, ...] = ()
    has_canonical: bool = False
    canonical_url: str | None = None
    robots: str | None = None
    noindex: bool = False
    has_json_ld: bool = False
    has_og_title: bool = False
    status_code: int | None = None
    language: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["h1_text"] = list(self.h1_text)
        payload["heading_outline"] = list(self.heading_outline)
        payload["same_domain_urls"] = list(self.same_domain_urls)
        return payload


@dataclass(frozen=True)
class SeoIssue:
    """One audit finding with copy-pasteable remediation for a human implementer."""

    issue_id: str
    severity: str
    title: str
    explanation: str
    how_to_fix: str
    evidence: str
    url: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditBundle:
    """Signals and issues for one or more pages on a target."""

    pages: list[PageSignals] = field(default_factory=list)
    issues: list[SeoIssue] = field(default_factory=list)
    score: int = 100
    crawled: bool = True
    crawl_error: str | None = None

    def as_evidence(self) -> dict[str, Any]:
        return {
            "pages": [page.as_dict() for page in self.pages],
            "score": self.score,
            "crawled": self.crawled,
            "crawl_error": self.crawl_error,
            "issue_count": len(self.issues),
        }


def _meta_str(metadata: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _meta_int(metadata: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def extract_page_signals(
    url: str,
    *,
    markdown: str | None,
    metadata: dict[str, Any] | None = None,
    title: str | None = None,
) -> PageSignals:
    """Pull on-page SEO signals from Firecrawl markdown and metadata."""
    meta = metadata or {}
    text = markdown or ""
    page_title = title or _meta_str(meta, "title", "ogTitle", "og:title")
    description = _meta_str(
        meta, "description", "ogDescription", "og:description", "metaDescription"
    )
    canonical = _meta_str(meta, "canonical", "canonicalUrl", "ogUrl", "og:url")
    robots = _meta_str(meta, "robots", "robotsTag", "x-robots-tag")
    language = _meta_str(meta, "language", "lang", "ogLocale", "og:locale")
    status = _meta_int(meta, "statusCode", "status", "httpStatus")
    og_title = _meta_str(meta, "ogTitle", "og:title")

    h1s = tuple(match.strip()[:200] for match in _H1_RE.findall(text) if match.strip())
    headings = tuple(
        f"{match[0]} {match[1].strip()[:120]}"
        for match in _HEADING_RE.findall(text)
        if match[1].strip()
    )[:20]

    images = _IMAGE_RE.findall(text)
    missing_alt = sum(1 for alt, _href in images if not alt.strip())

    domain = registrable_domain(url)
    internal = 0
    external = 0
    same_domain: list[str] = []
    seen_same: set[str] = set()
    for _label, href in _LINK_RE.findall(text):
        href = href.strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if registrable_domain(absolute) == domain:
            internal += 1
            clean = canonical_url(absolute)
            if clean not in seen_same and clean != canonical_url(url):
                seen_same.add(clean)
                same_domain.append(clean)
        else:
            external += 1

    haystack = f"{text}\n{page_title or ''}\n{description or ''}"
    has_json_ld = any(hint.lower() in haystack.lower() for hint in _JSON_LD_HINTS)
    if meta.get("jsonld") or meta.get("schema") or meta.get("structuredData"):
        has_json_ld = True

    robots_lower = (robots or "").lower()
    noindex = "noindex" in robots_lower
    words = _WORD_RE.findall(text)

    return PageSignals(
        url=canonical_url(url),
        title=page_title,
        title_length=len(page_title or ""),
        meta_description=description,
        meta_length=len(description or ""),
        h1_count=len(h1s),
        h1_text=h1s[:5],
        heading_outline=headings,
        word_count=len(words),
        image_count=len(images),
        images_missing_alt=missing_alt,
        internal_links=internal,
        external_links=external,
        same_domain_urls=tuple(same_domain[:12]),
        has_canonical=bool(canonical),
        canonical_url=canonical,
        robots=robots,
        noindex=noindex,
        has_json_ld=has_json_ld,
        has_og_title=bool(og_title or (page_title and meta.get("ogTitle"))),
        status_code=status,
        language=language,
    )


def detect_issues(page: PageSignals) -> list[SeoIssue]:
    """Map page signals to OpenSEO-style issues with how-to-fix steps."""
    issues: list[SeoIssue] = []
    url = page.url

    if page.status_code is not None and page.status_code >= 500:
        issues.append(
            _issue(
                "server-error",
                "critical",
                "Server error (5xx)",
                "The page returned a 5xx server error. Search engines that repeatedly "
                "see server errors crawl less and may drop the page from the index.",
                "Fix the server/application error, then request a recrawl in Search Console.",
                f"HTTP {page.status_code}",
                url,
            )
        )
    if page.status_code is not None and 400 <= page.status_code < 500:
        issues.append(
            _issue(
                "client-error",
                "critical",
                "Client error (4xx)",
                "The page returned a 4xx status. Search engines will not index an error page.",
                "Restore a 200 response or 301-redirect to the live URL that should rank.",
                f"HTTP {page.status_code}",
                url,
            )
        )
    if not page.title:
        issues.append(
            _issue(
                "missing-title",
                "high",
                "Missing title tag",
                "The page has no title. The title is the strongest on-page relevance "
                "signal and the headline shown in search results.",
                "Add a unique <title> of about 50-60 characters that names the page topic "
                "and the brand. Example: `Romance serials for mobile | MidnightSatin`.",
                "no <title> in scrape metadata",
                url,
            )
        )
    elif page.title_length > 65:
        issues.append(
            _issue(
                "title-too-long",
                "medium",
                "Title likely truncated in search results",
                "Titles much longer than ~60 characters are often cut off in the SERP.",
                "Shorten the title to 50-60 characters. Keep the primary keyword and brand; "
                f"current length is {page.title_length}.",
                f'title="{page.title}" ({page.title_length} chars)',
                url,
            )
        )
    elif page.title_length < 15:
        issues.append(
            _issue(
                "title-too-short",
                "medium",
                "Title is too short to describe the page",
                "A very short title gives search engines and people little to go on.",
                "Expand the title so it states the topic in a full phrase, not a label.",
                f'title="{page.title}" ({page.title_length} chars)',
                url,
            )
        )
    if not page.meta_description:
        issues.append(
            _issue(
                "missing-meta-description",
                "medium",
                "Missing meta description",
                "The meta description is the snippet under the title in search results. "
                "Without one, Google writes its own, often from a random sentence.",
                "Add a <meta name=\"description\"> of 120-155 characters that states the "
                "offer and a reason to click. Do not stuff keywords.",
                "no meta description in scrape metadata",
                url,
            )
        )
    elif page.meta_length > 165:
        issues.append(
            _issue(
                "meta-too-long",
                "low",
                "Meta description likely truncated",
                "Snippets longer than ~155-160 characters are often cut off.",
                f"Trim the description to 120-155 characters (currently {page.meta_length}).",
                f"meta description {page.meta_length} chars",
                url,
            )
        )
    if page.h1_count == 0:
        issues.append(
            _issue(
                "missing-h1",
                "high",
                "Missing H1 heading",
                "The H1 is the visible page headline and a primary relevance signal. "
                "Pages without one look unfinished and are harder to rank.",
                "Add one H1 that matches the search intent of the page. It can be close "
                "to the title, but should read as a heading, not a browser tab label.",
                "no markdown H1 (`# ...`)",
                url,
            )
        )
    elif page.h1_count > 1:
        issues.append(
            _issue(
                "multiple-h1",
                "low",
                "Multiple H1 headings",
                "More than one H1 dilutes the main topic. One H1 plus H2/H3 sections is clearer.",
                "Keep a single H1. Demote extras to H2.",
                f"H1s: {', '.join(page.h1_text) or page.h1_count}",
                url,
            )
        )
    if page.word_count < 150 and (page.status_code is None or page.status_code < 400):
        issues.append(
            _issue(
                "thin-content",
                "high",
                "Thin page content",
                "Very short pages rarely rank for non-branded queries and give AI search "
                "engines little to cite.",
                "Write a complete page: what it is, who it is for, proof, and a next step. "
                f"Current extract is ~{page.word_count} words.",
                f"{page.word_count} words extracted",
                url,
            )
        )
    if page.image_count > 0 and page.images_missing_alt:
        issues.append(
            _issue(
                "missing-alt",
                "medium",
                "Images missing alt text",
                "Alt text describes images for accessibility and image search. Missing alt "
                "is a common, cheap fix.",
                "Add short, specific alt text to each content image. Skip decorative icons.",
                f"{page.images_missing_alt} of {page.image_count} images missing alt",
                url,
            )
        )
    if page.noindex:
        issues.append(
            _issue(
                "noindex",
                "critical",
                "Page is marked noindex",
                "A noindex robots directive tells search engines not to list this URL. "
                "That is correct for thank-you pages; it is fatal for a homepage.",
                "If this URL should rank, remove noindex from the robots meta tag or "
                "X-Robots-Tag header and from robots.txt Disallow if present.",
                f"robots={page.robots}",
                url,
            )
        )
    if not page.has_canonical:
        issues.append(
            _issue(
                "missing-canonical",
                "low",
                "Missing canonical URL",
                "A canonical tag (the preferred URL when copies exist) prevents duplicate "
                "indexing of http/https or trailing-slash variants.",
                f"Add <link rel=\"canonical\" href=\"{url}\"> in the document head.",
                "no canonical in scrape metadata",
                url,
            )
        )
    if not page.has_json_ld:
        issues.append(
            _issue(
                "missing-schema",
                "medium",
                "No structured data (JSON-LD)",
                "Schema.org JSON-LD helps search engines and AI assistants extract the "
                "organization, product, or article. It is not a ranking guarantee.",
                "Add a JSON-LD <script type=\"application/ld+json\"> block for Organization "
                "or SoftwareApplication with name, url, and description. Never invent reviews.",
                "no JSON-LD / schema.org hints in page or metadata",
                url,
            )
        )
    if not page.has_og_title and page.title:
        issues.append(
            _issue(
                "missing-og",
                "low",
                "Missing Open Graph title",
                "og:title controls the headline when the page is shared. Search is less "
                "affected than social, but it is a cheap completeness fix.",
                f"Add <meta property=\"og:title\" content=\"{page.title}\">.",
                "no og:title in scrape metadata",
                url,
            )
        )
    return issues


def score_issues(issues: list[SeoIssue]) -> int:
    """Heuristic 0-100. Not a vendor ranking; say so in the review document."""
    score = 100
    for issue in issues:
        score -= SEVERITY_PENALTY.get(issue.severity, 4)
    return max(0, min(100, score))


def pick_one_thing(issues: list[SeoIssue], *, owned: bool) -> str:
    """OpenSEO audit skill: the whole report supports one action this week."""
    if not issues:
        if owned:
            return (
                "The crawled pages look technically healthy. This week, pick one "
                "specific, low-competition topic from the keyword notes and publish "
                "a real page or post that answers it. Do not chase head terms yet."
            )
        return (
            "No blocking technical issues were visible on the crawled competitor pages. "
            "Study their heading outline and title pattern; do not copy their copy."
        )
    ordered = sorted(
        issues,
        key=lambda item: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(
            item.severity, 4
        ),
    )
    top = ordered[0]
    return f"{top.title}: {top.how_to_fix}"


def related_paths_to_fetch(page: PageSignals, *, limit: int = 3) -> list[str]:
    """Prefer about/pricing/blog links already on the page (OpenSEO key-page idea)."""
    preferred = ("/about", "/pricing", "/blog", "/features", "/product", "/docs")
    ranked: list[tuple[int, str]] = []
    for href in page.same_domain_urls:
        path = urlparse(href).path.lower()
        rank = 50
        for index, needle in enumerate(preferred):
            if needle in path:
                rank = index
                break
        ranked.append((rank, href))
    ranked.sort(key=lambda item: (item[0], item[1]))
    urls: list[str] = []
    seen: set[str] = set()
    for _rank, href in ranked:
        if href in seen:
            continue
        seen.add(href)
        urls.append(href)
        if len(urls) >= limit:
            break
    return urls


def _issue(
    issue_id: str,
    severity: str,
    title: str,
    explanation: str,
    how_to_fix: str,
    evidence: str,
    url: str,
) -> SeoIssue:
    return SeoIssue(
        issue_id=issue_id,
        severity=severity,
        title=title,
        explanation=explanation,
        how_to_fix=how_to_fix,
        evidence=evidence,
        url=url,
    )
