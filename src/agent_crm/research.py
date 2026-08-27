"""Research agent: competitor, nonprofit, and ad-placement prospecting via SearXNG + Firecrawl + Spark."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select

from .config import get_settings
from .comment_people_store import process_scraped_page_comment_people
from .contact_store import ContactExtractionBudget, process_scraped_page_contacts
from .enums import ActivityType, AgentStatus, Brand, ResearchFindingKind
from .firecrawl_client import FirecrawlError, ScrapeResult, scrape
from .llm_client import chat_completions
from .llm_text import UNTRUSTED_DATA_SYSTEM_SUFFIX, wrap_untrusted
from .research_seeds import BRAND_DISPLAY, default_kind_for_brand, seed_queries
from .research_store import upsert_finding
from .research_utils import (
    canonical_url,
    clean_title,
    extract_domain,
    extract_ein_from_text,
    is_junk_finding,
    is_scrapable_url,
)
from .marketing_skill import (
    ad_placement_summarizer_guidance,
    brand_context_snippet,
    competitor_summarizer_guidance,
)
from .schemas import ResearchRequest, ResearchResult
from .searxng_client import SearchResult, SearxngError, search
from .tooling import CRMToolkit

ACTOR = "research"


@dataclass(frozen=True)
class ResearchBudget:
    max_queries: int
    max_pages: int
    max_minutes: int


def run_research(
    request: ResearchRequest,
    *,
    searx_client: httpx.Client | None = None,
    firecrawl_client: httpx.Client | None = None,
) -> ResearchResult:
    """Execute a bounded research run for one brand/kind."""
    settings = get_settings()
    crm = CRMToolkit(actor=ACTOR)

    kind = request.kind or default_kind_for_brand(request.brand)
    budget = ResearchBudget(
        max_queries=min(request.max_queries, settings.research_max_queries_default),
        max_pages=min(request.max_pages, settings.research_max_pages_per_run),
        max_minutes=min(request.max_minutes, settings.research_max_minutes_default),
    )
    search_limit = min(request.search_limit, settings.research_search_result_limit)

    queries = seed_queries(request.brand, kind, explicit_query=request.query)
    queries = queries[: budget.max_queries]

    started = time.monotonic()
    errors: list[str] = []
    findings_written: list[int] = []
    queries_run = 0
    pages_scraped = 0
    seen_urls: set[str] = set()
    contact_budget = ContactExtractionBudget.from_settings()

    crm.log_note(
        f"Research run started for {request.brand.value} ({kind.value})",
        type=ActivityType.NOTE,
        payload={"brand": request.brand.value, "kind": kind.value, "queries": len(queries)},
    )

    for query in queries:
        if queries_run >= budget.max_queries:
            break
        if _elapsed_minutes(started) >= budget.max_minutes:
            break
        if pages_scraped >= budget.max_pages:
            break

        queries_run += 1
        crm.record_heartbeat(
            status=AgentStatus.THINKING,
            task=f"searching: {query}",
            resource=settings.searxng_url,
        )

        try:
            results = search(query, limit=search_limit, client=searx_client)
        except SearxngError as exc:
            message = f"SearXNG search failed for {query!r}: {exc}"
            errors.append(message)
            crm.log_note(message, type=ActivityType.ERROR, payload={"query": query})
            continue

        for hit in results:
            if pages_scraped >= budget.max_pages:
                break
            if _elapsed_minutes(started) >= budget.max_minutes:
                break

            normalized = canonical_url(hit.url)
            if normalized in seen_urls:
                continue
            if not is_scrapable_url(normalized):
                continue
            if is_junk_finding(title=hit.title, snippet=hit.snippet):
                continue

            seen_urls.add(normalized)

            crm.record_heartbeat(
                status=AgentStatus.WORKING,
                task=f"scraping {normalized}",
                resource=settings.firecrawl_url,
            )

            try:
                page = scrape(normalized, client=firecrawl_client)
            except FirecrawlError as exc:
                message = f"Firecrawl scrape failed for {normalized}: {exc}"
                errors.append(message)
                crm.log_note(message, type=ActivityType.ERROR, payload={"url": normalized})
                continue

            title = clean_title(page.title or hit.title or extract_domain(normalized))
            if is_junk_finding(title=title, snippet=hit.snippet, markdown=page.markdown):
                continue

            pages_scraped += 1
            fallback = _fallback_summary(hit, page, kind, request.brand)
            summary = fallback
            extra: dict[str, Any] | None = None

            if request.summarize:
                crm.record_heartbeat(
                    status=AgentStatus.WORKING,
                    task=f"summarizing {normalized}",
                    resource=f"Spark queue ({settings.llm_base_url})",
                )
                summary, extra = _maybe_summarize(
                    hit,
                    page,
                    fallback,
                    brand=request.brand,
                    kind=kind,
                    errors=errors,
                )

            if extra is None:
                extra = _heuristic_extra(page, hit, kind)

            finding = upsert_finding(
                url=normalized,
                title=title,
                brand=request.brand,
                kind=kind,
                summary=summary,
                source_query=query,
                raw_snippet=hit.snippet or None,
                extra=extra,
            )
            findings_written.append(finding.id)

            if request.write_accounts and _is_strong_hit(summary, extra):
                _maybe_write_account_note(crm, normalized, title, summary, extra)

            try:
                process_scraped_page_contacts(
                    markdown=page.markdown,
                    source_url=normalized,
                    brand=request.brand,
                    searx_client=searx_client,
                    budget=contact_budget,
                )
                process_scraped_page_comment_people(
                    markdown=page.markdown,
                    source_url=normalized,
                    brand=request.brand,
                    budget=contact_budget,
                )
            except Exception:  # noqa: BLE001
                pass

    crm.record_heartbeat(status=AgentStatus.IDLE)
    return ResearchResult(
        brand=request.brand,
        kind=kind,
        queries_run=queries_run,
        pages_scraped=pages_scraped,
        findings_written=findings_written,
        errors=errors,
    )


def _elapsed_minutes(started: float) -> float:
    return (time.monotonic() - started) / 60.0


def _fallback_summary(
    hit: SearchResult,
    page: ScrapeResult,
    kind: ResearchFindingKind,
    brand: Brand,
) -> str:
    brand_label = BRAND_DISPLAY.get(brand, brand.value)
    title = page.title or hit.title or hit.url
    body = (page.markdown or "").strip()[:1200]
    if kind == ResearchFindingKind.COMPETITOR:
        prefix = f"Competitor site vs {brand_label}: "
    elif kind == ResearchFindingKind.NONPROFIT:
        prefix = "Potential nonprofit / 501(c)(3) partner for HeyBuddy: "
    elif kind == ResearchFindingKind.AD_PLACEMENT:
        prefix = f"Ad placement opportunity for {brand_label}: "
    else:
        prefix = "Research finding: "
    parts = [prefix + title]
    if hit.snippet:
        parts.append(hit.snippet)
    if body:
        parts.append(body)
    return "\n\n".join(parts).strip()


def _maybe_summarize(
    hit: SearchResult,
    page: ScrapeResult,
    fallback: str,
    *,
    brand: Brand,
    kind: ResearchFindingKind,
    errors: list[str],
) -> tuple[str, dict[str, Any] | None]:
    brand_label = BRAND_DISPLAY.get(brand, brand.value)
    brand_context = brand_context_snippet(brand)
    page_block = (
        f"{wrap_untrusted('url', hit.url, max_chars=500)}\n"
        f"{wrap_untrusted('title', page.title or hit.title, max_chars=300)}\n"
        f"{wrap_untrusted('snippet', hit.snippet, max_chars=500)}\n"
        f"{wrap_untrusted('page_excerpt', page.markdown, max_chars=3500)}"
    )
    if kind == ResearchFindingKind.COMPETITOR:
        system = (
            "You analyze competitor websites for a CRM research agent. "
            "Summarize positioning, audience, and product angle vs the target brand. "
            "Be factual; do not invent contact details, stats, or testimonials.\n\n"
            f"{competitor_summarizer_guidance()}"
            + UNTRUSTED_DATA_SYSTEM_SUFFIX
        )
        if brand_context:
            system += f"\n\n--- brand context (excerpt) ---\n{brand_context}"
        user = (
            f"Target brand: {brand_label}\n"
            f"{page_block}\n\n"
            'Return JSON: {"summary": "...", "why_it_matters": "..."}'
        )
    elif kind == ResearchFindingKind.NONPROFIT:
        system = (
            "You analyze US nonprofits and 501(c)(3)-adjacent organizations for partnership "
            "prospecting with an AI companion app (HeyBuddy). Focus on mission overlap: "
            "loneliness, mental wellness, elder companionship, veterans, youth digital wellbeing. "
            "Only include ein if it appears verbatim in the source text; never invent tax status."
            + UNTRUSTED_DATA_SYSTEM_SUFFIX
        )
        user = (
            f"{page_block}\n\n"
            'Return JSON: {"summary": "...", "org_name": "...", "mission": "...", '
            '"ein": null or "XX-XXXXXXX", "why_it_matters": "..."}'
        )
    elif kind == ResearchFindingKind.AD_PLACEMENT:
        system = (
            "You analyze websites, forums, newsletters, podcasts, and communities that sell ads, "
            "take sponsorships, or offer promo/sticky/banner/board placement. "
            "Discovery only — do not invent pricing or contact emails. "
            "Assess brand fit and brand safety honestly (imageboards like 4chan often warrant caution).\n\n"
            f"{ad_placement_summarizer_guidance()}"
            + UNTRUSTED_DATA_SYSTEM_SUFFIX
        )
        if brand_context:
            system += f"\n\n--- brand context (excerpt) ---\n{brand_context}"
        user = (
            f"Target brand: {brand_label}\n"
            f"{page_block}\n\n"
            'Return JSON: {"summary": "...", "site_name": "...", "audience": "...", '
            '"ad_product": "display|newsletter|sticky|board|sponsorship|discord boost|other", '
            '"how_to_buy": "URL or unknown", "brand_fit": "...", '
            '"brand_safety": "ok|caution|avoid — brief reason", "why_it_matters": "..."}'
        )
    else:
        system = "You write concise CRM research summaries. Be factual." + UNTRUSTED_DATA_SYSTEM_SUFFIX
        user = page_block

    try:
        response = chat_completions(
            {
                "model": "crm",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 320,
                "temperature": 0.2,
            },
            timeout=120.0,
            actor=ACTOR,
            task=f"summarize {hit.url}",
        )
        content = _extract_chat_content(response)
        if not content:
            return fallback, _heuristic_extra(page, hit, kind)

        parsed = _parse_json_object(content)
        if parsed:
            summary = str(parsed.get("summary") or fallback).strip()
            extra = _normalize_extra(parsed, page, hit, kind)
            return summary, extra
        if content.strip():
            return content.strip(), _heuristic_extra(page, hit, kind)
    except Exception as exc:  # noqa: BLE001 — research must not block on LLM failure
        errors.append(f"LLM summary failed for {hit.url}: {exc}")

    return fallback, _heuristic_extra(page, hit, kind)


def _heuristic_extra(
    page: ScrapeResult,
    hit: SearchResult,
    kind: ResearchFindingKind,
) -> dict[str, Any] | None:
    text = " ".join(
        part
        for part in (
            page.title or "",
            hit.title or "",
            hit.snippet or "",
            (page.markdown or "")[:4000],
        )
        if part
    )
    if kind == ResearchFindingKind.NONPROFIT:
        ein = extract_ein_from_text(text)
        extra: dict[str, Any] = {}
        if ein:
            extra["ein"] = ein
        org_name = clean_title(page.title or hit.title or "")
        if org_name:
            extra["org_name"] = org_name
        return extra or None

    if kind == ResearchFindingKind.AD_PLACEMENT:
        return _heuristic_ad_placement_extra(page, hit, text)

    return None


_AD_PRODUCT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("newsletter", "newsletter"),
    ("sponsor", "sponsorship"),
    ("sponsorship", "sponsorship"),
    ("media kit", "sponsorship"),
    ("sticky", "sticky"),
    ("banner", "display"),
    ("display ad", "display"),
    ("self promote", "board"),
    ("self-promotion", "board"),
    ("promo board", "board"),
    ("discord boost", "discord boost"),
    ("advertise", "display"),
    ("advertising", "display"),
)

_BUY_LINK_HINTS = ("advertise", "sponsor", "media-kit", "mediakit", "ads.", "/ads")


def _heuristic_ad_placement_extra(
    page: ScrapeResult,
    hit: SearchResult,
    text: str,
) -> dict[str, Any]:
    lower = text.lower()
    extra: dict[str, Any] = {}
    site_name = clean_title(page.title or hit.title or extract_domain(hit.url))
    if site_name:
        extra["site_name"] = site_name

    for keyword, product in _AD_PRODUCT_KEYWORDS:
        if keyword in lower:
            extra["ad_product"] = product
            break
    if "ad_product" not in extra:
        extra["ad_product"] = "other"

    how_to_buy = _extract_how_to_buy(page.markdown or "", hit.url)
    extra["how_to_buy"] = how_to_buy

    domain = extract_domain(hit.url).lower()
    if "4chan" in domain or "8kun" in domain:
        extra["brand_safety"] = "caution — imageboard; high toxicity and moderation risk"
    elif any(token in domain for token in ("reddit.com", "discord.")):
        extra["brand_safety"] = "caution — community platform; review sub/server rules"
    else:
        extra["brand_safety"] = "ok — review placement context before buying"

    if hit.snippet:
        extra["audience"] = hit.snippet.strip()[:240]
    extra["why_it_matters"] = "Potential paid placement surface discovered via research seed."
    return extra


def _extract_how_to_buy(markdown: str, page_url: str) -> str:
    for line in markdown.splitlines():
        lower = line.lower()
        if not any(hint in lower for hint in _BUY_LINK_HINTS):
            continue
        match = re.search(r"https?://[^\s)>\"]+", line)
        if match:
            return match.group(0).rstrip(".,)")
    if any(hint in page_url.lower() for hint in _BUY_LINK_HINTS):
        return page_url
    return "unknown"


def _normalize_extra(
    parsed: dict[str, Any],
    page: ScrapeResult,
    hit: SearchResult,
    kind: ResearchFindingKind,
) -> dict[str, Any] | None:
    extra: dict[str, Any] = {}
    for key in (
        "org_name",
        "mission",
        "why_it_matters",
        "site_name",
        "audience",
        "ad_product",
        "how_to_buy",
        "brand_fit",
        "brand_safety",
    ):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            extra[key] = value.strip()

    if kind == ResearchFindingKind.NONPROFIT:
        text = " ".join(
            part
            for part in (
                page.title or "",
                hit.snippet or "",
                (page.markdown or "")[:4000],
            )
            if part
        )
        source_ein = extract_ein_from_text(text)
        if source_ein:
            extra["ein"] = source_ein

    if kind == ResearchFindingKind.AD_PLACEMENT and "how_to_buy" not in extra:
        how_to_buy = _extract_how_to_buy(page.markdown or "", hit.url)
        if how_to_buy != "unknown":
            extra["how_to_buy"] = how_to_buy

    return extra or None


def _parse_json_object(content: str) -> dict[str, Any] | None:
    from .llm_text import extract_json_object

    return extract_json_object(content)


def _extract_chat_content(response: dict[str, Any]) -> str | None:
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
    return content if isinstance(content, str) else None


def _is_strong_hit(summary: str, extra: dict[str, Any] | None) -> bool:
    if len(summary) >= 120:
        return True
    if extra and any(
        extra.get(key)
        for key in (
            "ein",
            "mission",
            "why_it_matters",
            "ad_product",
            "how_to_buy",
            "brand_fit",
        )
    ):
        return True
    return False


def _maybe_write_account_note(
    crm: CRMToolkit,
    url: str,
    title: str,
    summary: str,
    extra: dict[str, Any] | None,
) -> None:
    """Optional pipeline visibility: account note + activity without fake emails."""
    from .db import session_scope
    from .models import Account
    from .research_utils import canonical_url

    domain = extract_domain(url)
    org_name = None
    if extra:
        org_name = extra.get("org_name") or extra.get("site_name")
    account_name = org_name or title or domain
    site_key = canonical_url(url) or url

    with session_scope() as session:
        account = session.scalar(
            select(Account).where(Account.website.in_([site_key, url])).limit(1)
        )
        if account is None and domain:
            candidates = list(
                session.scalars(
                    select(Account)
                    .where(Account.website.is_not(None))
                    .where(Account.website.contains(domain))
                    .limit(50)
                )
            )
            for row in candidates:
                if extract_domain(row.website or "") == domain:
                    account = row
                    break
        if account is None:
            account = Account(name=str(account_name)[:255], website=site_key)
            session.add(account)
        else:
            if org_name or (
                title and len(str(account_name)) > len(account.name or "")
            ):
                account.name = str(account_name)[:255]
            account.website = site_key

        note_parts = [summary[:2000]]
        if extra:
            if extra.get("mission"):
                note_parts.append(f"Mission: {extra['mission']}")
            if extra.get("ein"):
                note_parts.append(f"EIN: {extra['ein']}")
            if extra.get("ad_product"):
                note_parts.append(f"Ad product: {extra['ad_product']}")
            if extra.get("how_to_buy"):
                note_parts.append(f"How to buy: {extra['how_to_buy']}")
            if extra.get("brand_safety"):
                note_parts.append(f"Brand safety: {extra['brand_safety']}")
            if extra.get("why_it_matters"):
                note_parts.append(f"Why it matters: {extra['why_it_matters']}")
        account.notes = "\n\n".join(note_parts)
        session.flush()

    crm.log_note(
        f"Research hit recorded for {domain}",
        type=ActivityType.NOTE,
        payload={"url": url, "account_name": account_name, "extra": extra},
    )
