"""Hunt / page topical relevance: deterministic gates + Spark-assisted decisions."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from agent_crm.enums import Brand, TopicalRelevanceVerdict
from agent_crm.llm_client import chat_completions
from agent_crm.llm_text import UNTRUSTED_DATA_SYSTEM_SUFFIX, extract_json_object, wrap_untrusted
from agent_crm.marketing_skill import brand_context_snippet

logger = logging.getLogger(__name__)

ACTOR = "hunt-relevance"

# Obvious popularity/docs/aggregator noise — never spend GPU unless a page is
# clearly on-topic, and never persist even then (deny-list always wins).
ALWAYS_OFF_TOPIC_HOSTS: frozenset[str] = frozenset(
    {
        "mozilla.org",
        "developer.mozilla.org",
        "w3.org",
        "www.w3.org",
        "wikipedia.org",
        "en.wikipedia.org",
        "stackoverflow.com",
        "stackexchange.com",
        "npmjs.com",
        "pypi.org",
        "docs.python.org",
        "developer.apple.com",
        "play.google.com",
        "apps.apple.com",
        "chrome.google.com",
        "support.google.com",
        "learn.microsoft.com",
        "docs.microsoft.com",
        "apache.org",
        "ietf.org",
        "gnu.org",
        "docker.com",
        "hub.docker.com",
        "haskell.org",
        "hackage.haskell.org",
        "reuters.com",
        "flipboard.com",
        "msn.com",
        "yahoo.com",
        "forbes.com",
        "wsj.com",
        "bloomberg.com",
        "dictionary.com",
        "merriam-webster.com",
        "britannica.com",
        "sportsbots.xyz",
    }
)

# Suffix-match these even when the host is a subdomain (hub.docker.com, wiki.haskell.org).
DENIED_HOST_SUFFIXES: frozenset[str] = frozenset(
    {
        "mozilla.org",
        "wikipedia.org",
        "w3.org",
        "docker.com",
        "haskell.org",
        "stackoverflow.com",
        "stackexchange.com",
        "reuters.com",
        "flipboard.com",
        "msn.com",
        "yahoo.com",
        "forbes.com",
        "wsj.com",
        "dictionary.com",
        "merriam-webster.com",
        "britannica.com",
    }
)

DOCS_PATH_FRAGMENTS: tuple[str, ...] = (
    "/docs/",
    "/documentation/",
    "/reference/",
    "/api/",
    "/wiki/",
    "/help/",
    "/support/",
    "/legal/",
    "/privacy",
    "/terms",
)

BRAND_TOPIC_SUMMARIES: dict[Brand, str] = {
    Brand.MIDNIGHTSATIN: (
        "romance writing and romance reading: booktok, spicy romance, "
        "serialized fiction, romance readers/authors, romance communities"
    ),
    Brand.CELESTIAL_NEXUS: (
        "horoscope, astrology, natal chart, zodiac, divination, tarot, "
        "spiritual astrology communities"
    ),
    Brand.HEYBUDDY: (
        "men's interests: hobbies, lifestyle, fitness, gear, dating, "
        "male-oriented communities and creators"
    ),
    Brand.TACTIC_STUDIO: (
        "marketing and brand leadership at large retail and food & beverage "
        "companies (more than $10 million annual revenue): VP of marketing, "
        "marketing managers, brand managers, brand management; also WebAR "
        "and brand AR activations"
    ),
}

BRAND_ON_TOPIC_KEYWORDS: dict[Brand, tuple[str, ...]] = {
    Brand.MIDNIGHTSATIN: (
        "romance",
        "booktok",
        "spicy romance",
        "dark romance",
        "romance reader",
        "romance author",
        "romance book",
        "romance novel",
        "romance community",
        "book blog",
        "booktok community",
    ),
    Brand.CELESTIAL_NEXUS: (
        "horoscope",
        "astrology",
        "natal chart",
        "zodiac",
        "divination",
        "tarot",
        "birth chart",
        "astrology community",
        "spiritual astrology",
    ),
    Brand.HEYBUDDY: (
        "men's",
        "mens",
        "male",
        "guys",
        "brotherhood",
        "masculinity",
        "men interest",
        "men's lifestyle",
        "men's hobbies",
    ),
    Brand.TACTIC_STUDIO: (
        "vp of marketing",
        "vice president of marketing",
        "vice president marketing",
        "brand manager",
        "marketing manager",
        "brand management",
        "head of marketing",
        "director of marketing",
        "chief marketing officer",
        "vp of sales",
        "vice president of sales",
        "vice president sales",
        "food and beverage",
        "food & beverage",
        "grocery chain",
        "retail marketing",
        "consumer packaged goods",
        "augmented reality",
        "webxr",
        "webar",
        "ar glasses",
        "mixed reality",
        "snap ar",
        "8th wall",
        "ar campaign",
        "ar activation",
    ),
}


@dataclass
class RelevanceAssessment:
    verdict: TopicalRelevanceVerdict
    reason: str
    spark_used: bool = False


def _host(url: str) -> str:
    return urlparse(url.strip()).netloc.lower().removeprefix("www.")


def _path(url: str) -> str:
    return urlparse(url.strip()).path.lower()


def _normalize_host(host: str) -> str:
    host = host.lower().removeprefix("www.")
    if host.endswith(".mozilla.org"):
        return "mozilla.org"
    if host.endswith(".wikipedia.org"):
        return "wikipedia.org"
    if host.endswith(".docker.com"):
        return "docker.com"
    if host.endswith(".haskell.org"):
        return "haskell.org"
    return host


def denied_host_reason(url: str) -> str | None:
    """Return a rejection reason when the registrable host is on the deny-list."""
    host = _normalize_host(_host(url))
    if not host:
        return "invalid URL"
    if host in ALWAYS_OFF_TOPIC_HOSTS:
        return f"generic docs/popularity domain: {host}"
    for suffix in DENIED_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return f"generic docs/popularity domain: {host}"
    return None


def is_obvious_off_topic_url(url: str) -> str | None:
    """Return a rejection reason when the URL is clearly generic noise."""
    denied = denied_host_reason(url)
    if denied:
        return denied
    host = _normalize_host(_host(url))
    path = _path(url)
    if host == "github.com" and any(fragment in path for fragment in DOCS_PATH_FRAGMENTS):
        return "github documentation page"
    if "/amp" in path and host in {"google.com", "www.google.com"}:
        return "google amp aggregator page"
    if any(fragment in path for fragment in DOCS_PATH_FRAGMENTS):
        if host in {"github.com", "gitlab.com", "bitbucket.org"}:
            return f"repository documentation path on {host}"
    return None


def _text_blob(*parts: str | None) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip()).lower()


def _keyword_on_topic_score(brand: Brand, text: str) -> int:
    keywords = BRAND_ON_TOPIC_KEYWORDS.get(brand, ())
    return sum(1 for keyword in keywords if keyword in text)


def assess_topical_relevance(
    *,
    brand: Brand,
    url: str,
    title: str | None = None,
    snippet: str | None = None,
    page_excerpt: str | None = None,
    query: str | None = None,
    allow_spark: bool = True,
) -> RelevanceAssessment:
    """Decide whether a page is on-brand for ``brand``."""
    if brand == Brand.UNASSIGNED:
        return RelevanceAssessment(
            verdict=TopicalRelevanceVerdict.UNCERTAIN,
            reason="brand unassigned",
        )

    obvious = is_obvious_off_topic_url(url)
    if obvious:
        return RelevanceAssessment(
            verdict=TopicalRelevanceVerdict.OFF_TOPIC,
            reason=obvious,
        )

    # Score the page, never the search query — seed queries already contain
    # brand keywords and would otherwise mark every SERP hit on-topic.
    text = _text_blob(title, snippet, page_excerpt)
    keyword_hits = _keyword_on_topic_score(brand, text)

    if keyword_hits >= 2:
        return RelevanceAssessment(
            verdict=TopicalRelevanceVerdict.ON_TOPIC,
            reason=f"on-topic keywords matched ({keyword_hits} signals)",
        )

    if keyword_hits == 1:
        return RelevanceAssessment(
            verdict=TopicalRelevanceVerdict.ON_TOPIC,
            reason="single strong on-topic keyword in page context",
        )

    if not allow_spark:
        return RelevanceAssessment(
            verdict=TopicalRelevanceVerdict.UNCERTAIN,
            reason="insufficient context for deterministic match",
        )

    return _spark_topical_assessment(
        brand=brand,
        url=url,
        title=title,
        snippet=snippet,
        page_excerpt=page_excerpt,
        query=query,
    )


def _spark_topical_assessment(
    *,
    brand: Brand,
    url: str,
    title: str | None,
    snippet: str | None,
    page_excerpt: str | None,
    query: str | None,
) -> RelevanceAssessment:
    topic = BRAND_TOPIC_SUMMARIES.get(brand, brand.value)
    context = brand_context_snippet(brand, max_chars=400)
    prompt = (
        "Decide if this web page is actually about the brand hunt topic, "
        "not just ranking because of popularity or generic tech/docs noise.\n"
        f"Brand topic: {topic}\n"
        f"Search query (if any): {query or 'n/a'}\n"
        f"{wrap_untrusted('url', url, max_chars=500)}\n"
        f"{wrap_untrusted('title', title, max_chars=300)}\n"
        f"{wrap_untrusted('snippet', snippet, max_chars=500)}\n"
        f"{wrap_untrusted('page_excerpt', page_excerpt, max_chars=1500)}\n"
    )
    if context:
        prompt += f"\nBrand context:\n{context}\n"
    prompt += (
        "\nReturn JSON only: "
        '{"verdict":"on_topic|off_topic|uncertain","reason":"short reason"}. '
        "Reject mozilla.org-style docs, app stores, and encyclopedia pages unless "
        "the article itself is clearly about the brand topic."
    )
    try:
        response = chat_completions(
            {
                "model": "crm",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You classify hunt result relevance. Output JSON only."
                            + UNTRUSTED_DATA_SYSTEM_SUFFIX
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 200,
            },
            timeout=90.0,
            actor=ACTOR,
            task=f"topical relevance {brand.value}",
        )
        content = response["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        logger.exception("Spark topical relevance failed for %s", url)
        return RelevanceAssessment(
            verdict=TopicalRelevanceVerdict.UNCERTAIN,
            reason="spark classification unavailable",
        )

    payload = extract_json_object(content)
    if not payload:
        return RelevanceAssessment(
            verdict=TopicalRelevanceVerdict.UNCERTAIN,
            reason="spark returned non-JSON",
            spark_used=True,
        )

    raw = str(payload.get("verdict", "")).strip().lower()
    reason = str(payload.get("reason", "spark classification")).strip()[:500]
    try:
        verdict = TopicalRelevanceVerdict(raw)
    except ValueError:
        verdict = TopicalRelevanceVerdict.UNCERTAIN
    return RelevanceAssessment(verdict=verdict, reason=reason, spark_used=True)


def fetch_public_page_excerpt(
    url: str,
    *,
    client: httpx.Client | None = None,
    max_chars: int = 4000,
) -> tuple[str | None, str | None, int | None]:
    """HTTP GET a public page and return title, excerpt, status."""
    from agent_crm.url_safety import UnsafeURLError, assert_public_http_url

    try:
        assert_public_http_url(url, resolve_dns=True)
    except UnsafeURLError:
        return None, None, None

    owns_client = client is None
    if owns_client:
        client = httpx.Client(follow_redirects=True, timeout=20.0)
    try:
        response = client.get(url)
        # Re-check final URL after redirects.
        try:
            assert_public_http_url(str(response.url), resolve_dns=True)
        except UnsafeURLError:
            return None, None, None
        status = response.status_code
        if status >= 400:
            return None, None, status
        html = response.text[: max_chars * 2]
        title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else None
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return title, text[:max_chars], status
    except httpx.HTTPError:
        return None, None, None
    finally:
        if owns_client and client is not None:
            client.close()


def is_hunt_result_relevant(
    *,
    brand: Brand,
    url: str,
    title: str | None,
    snippet: str | None,
    query: str | None,
    allow_spark: bool = True,
) -> bool:
    """Return True when a SERP hit should be scraped / collected."""
    assessment = assess_topical_relevance(
        brand=brand,
        url=url,
        title=title,
        snippet=snippet,
        query=query,
        allow_spark=allow_spark,
    )
    return assessment.verdict == TopicalRelevanceVerdict.ON_TOPIC
