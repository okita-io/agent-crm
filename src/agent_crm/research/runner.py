"""Research agent: competitor, nonprofit, ad-placement, and target-company prospecting via SearXNG + Firecrawl + Spark."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select

from agent_crm.contacts.comment_people_store import process_scraped_page_comment_people
from agent_crm.config import get_settings
from agent_crm.contacts.store import ContactExtractionBudget, process_scraped_page_contacts
from agent_crm.engagement.runner import ENGAGEMENT_VENUE_KINDS
from agent_crm.enums import ActivityType, AgentStatus, Brand, ResearchFindingKind, ResearchQueryStatus, TopicalRelevanceVerdict
from agent_crm.firecrawl_client import FirecrawlError, ScrapeResult, scrape
from agent_crm.hunt.relevance import assess_topical_relevance, is_obvious_off_topic_url
from agent_crm.hunt.store import HuntStore
from agent_crm.hunt.utils import classify_resource_detailed
from agent_crm.llm_client import chat_completions
from agent_crm.llm_text import UNTRUSTED_DATA_SYSTEM_SUFFIX, extract_json_object, wrap_untrusted
from agent_crm.skill_runtime import (
    brand_context_for,
    research_ad_placement_guidance,
    research_competitor_guidance,
)
from .feedback import extract_research_follow_up_terms
from .query_store import ResearchQueryStore
from .seeds import BRAND_DISPLAY, default_kind_for_brand, seed_queries
from .store import upsert_finding
from .utils import (
    canonical_url,
    clean_title,
    extract_domain,
    extract_ein_from_text,
    is_junk_finding,
    is_scrapable_url,
)
from agent_crm.schemas import ResearchRequest, ResearchResult
from agent_crm.searxng_client import SearchResult, SearxngError
from .target_companies import (
    companies_from_payload,
    enqueue_target_company_hunts,
    heuristic_companies_from_title,
)
from agent_crm.tooling import CRMToolkit
from agent_crm.treg.client import TregError, treg_base_url
from agent_crm.treg.search import collect_search_results, treg_endpoint_from

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

    store = ResearchQueryStore()
    started = time.monotonic()
    errors: list[str] = []
    findings_written: list[int] = []
    queries_run = 0
    pages_scraped = 0
    follow_up_terms_enqueued = 0
    seen_urls: set[str] = set()
    contact_budget = ContactExtractionBudget.from_settings()
    explicit_work: list[tuple[str, int | None]] | None = None

    if request.query and request.query.strip():
        query = request.query.strip()
        store.enqueue_query(
            query=query,
            brand=request.brand,
            kind=kind,
            origin="explicit",
        )
        row = store.get_by_dedupe(request.brand, kind, query)
        if row is not None and row.status == ResearchQueryStatus.PENDING:
            store.mark_query_running(row.id)
        explicit_work = [(query, row.id if row is not None else None)]
    else:
        for seed in seed_queries(request.brand, kind):
            store.enqueue_query(
                query=seed,
                brand=request.brand,
                kind=kind,
                origin="seed_pack",
            )

    crm.log_note(
        f"Research run started for {request.brand.value} ({kind.value})",
        type=ActivityType.NOTE,
        payload={
            "brand": request.brand.value,
            "kind": kind.value,
            "queued": store.count_pending(brand=request.brand, kind=kind),
        },
    )

    explicit_index = 0
    while True:
        if queries_run >= budget.max_queries:
            break
        if _elapsed_minutes(started) >= budget.max_minutes:
            break
        if pages_scraped >= budget.max_pages:
            break

        query_id: int | None
        origin = "explicit"
        if explicit_work is not None:
            if explicit_index >= len(explicit_work):
                break
            query, query_id = explicit_work[explicit_index]
            explicit_index += 1
        else:
            claimed = store.claim_next_pending_query(brand=request.brand, kind=kind)
            if claimed is None:
                break
            query, query_id = claimed.query, claimed.id
            origin = claimed.origin

        queries_run += 1
        treg_id = treg_endpoint_from(origin, None)
        crm.record_heartbeat(
            status=AgentStatus.THINKING,
            task=f"searching: {query}",
            resource=treg_base_url() if treg_id else settings.searxng_url,
        )

        try:
            results = collect_search_results(
                query,
                limit=search_limit,
                origin=origin,
                searx_client=searx_client,
            )
        except (SearxngError, TregError) as exc:
            message = f"Search failed for {query!r}: {exc}"
            errors.append(message)
            crm.log_note(message, type=ActivityType.ERROR, payload={"query": query})
            if query_id is not None:
                store.mark_query_failed(query_id, message)
            continue

        serp_dicts = [
            {"title": hit.title, "url": hit.url, "content": hit.snippet} for hit in results
        ]
        page_texts: list[str] = []

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
            if is_obvious_off_topic_url(normalized):
                continue
            if request.brand != Brand.UNASSIGNED:
                assessment = assess_topical_relevance(
                    brand=request.brand,
                    url=normalized,
                    title=hit.title,
                    snippet=hit.snippet,
                    query=query,
                    allow_spark=False,
                )
                if assessment.verdict == TopicalRelevanceVerdict.OFF_TOPIC:
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
            if page.markdown:
                page_texts.append(page.markdown[:8000])
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

            if kind == ResearchFindingKind.AD_PLACEMENT:
                extra = _catalog_engagement_venue(
                    url=normalized,
                    title=title,
                    brand=request.brand,
                    query=query,
                    snippet=page.markdown or hit.snippet,
                    extra=extra,
                )

            if kind == ResearchFindingKind.TARGET_COMPANY:
                extra = _ensure_target_company_extra(extra, title=title)
                enqueue_target_company_hunts(extra=extra, brand=request.brand)

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
                companies = extra.get("companies") if extra else None
                if not (isinstance(companies, list) and len(companies) > 1):
                    _maybe_write_account_note(crm, normalized, title, summary, extra)

            try:
                if kind != ResearchFindingKind.AD_PLACEMENT:
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

        follow_up_terms_enqueued += _enqueue_research_follow_ups(
            store,
            query=query,
            brand=request.brand,
            kind=kind,
            serp_results=serp_dicts,
            page_texts=page_texts,
            summarize=request.summarize,
            errors=errors,
        )
        if query_id is not None:
            store.mark_query_completed(query_id)

    crm.record_heartbeat(status=AgentStatus.IDLE)
    return ResearchResult(
        brand=request.brand,
        kind=kind,
        queries_run=queries_run,
        pages_scraped=pages_scraped,
        findings_written=findings_written,
        errors=errors,
        follow_up_terms_enqueued=follow_up_terms_enqueued,
    )


def _elapsed_minutes(started: float) -> float:
    return (time.monotonic() - started) / 60.0


def _enqueue_research_follow_ups(
    store: ResearchQueryStore,
    *,
    query: str,
    brand: Brand,
    kind: ResearchFindingKind,
    serp_results: list[dict[str, Any]],
    page_texts: list[str],
    summarize: bool,
    errors: list[str],
) -> int:
    """Append discovered search terms to the research queue. Never deletes."""
    settings = get_settings()
    max_terms = settings.research_max_branch_terms
    heuristic = extract_research_follow_up_terms(
        query=query,
        brand=brand,
        kind=kind,
        serp_results=serp_results,
        page_texts=page_texts,
        max_terms=max_terms,
    )
    llm_terms: list[str] = []
    if summarize:
        llm_terms = _llm_research_follow_up_terms(
            query=query,
            brand=brand,
            kind=kind,
            serp_results=serp_results,
            page_texts=page_texts,
            max_terms=max_terms,
            errors=errors,
        )

    merged: list[str] = []
    seen: set[str] = {ResearchQueryStore.make_dedupe_key(brand, kind, query)}
    for term in heuristic + llm_terms:
        key = ResearchQueryStore.make_dedupe_key(brand, kind, term)
        if key in seen:
            continue
        seen.add(key)
        merged.append(term)
        if len(merged) >= max_terms:
            break

    origin = f"branch:{query}".strip()[:128]
    enqueued = 0
    for term in merged:
        if store.enqueue_query(query=term, brand=brand, kind=kind, origin=origin):
            enqueued += 1
    return enqueued


def _llm_research_follow_up_terms(
    *,
    query: str,
    brand: Brand,
    kind: ResearchFindingKind,
    serp_results: list[dict[str, Any]],
    page_texts: list[str],
    max_terms: int,
    errors: list[str],
) -> list[str]:
    brand_label = BRAND_DISPLAY.get(brand, brand.value)
    lines = []
    for idx, item in enumerate(serp_results[:12], start=1):
        lines.append(
            wrap_untrusted(
                f"hit_{idx}",
                f"{item.get('title', '')} | {item.get('url', '')} | "
                f"{(item.get('content') or '')[:200]}",
                max_chars=280,
            )
        )
    for idx, text in enumerate(page_texts[:4], start=1):
        lines.append(wrap_untrusted(f"page_{idx}", text, max_chars=1200))

    prompt = (
        f"You help a CRM research agent expand its search queue for {brand_label} "
        f"({kind.value}).\n"
        f"Original query: {wrap_untrusted('query', query, max_chars=300)}\n"
        "Search hits and page excerpts:\n"
        + "\n".join(lines)
        + "\n\n"
        + (
            f"Suggest up to {max_terms} NEW search queries for more named retail, "
            "grocery, food & beverage, CPG, restaurant, or convenience-store companies "
            "over $10 million revenue. Prefer lists, directories, and rankings. "
            "Do NOT invent company names, emails, or URLs. Skip news headlines, "
        "product recalls, sports, and weather — they are off-topic.\n"
            if kind == ResearchFindingKind.TARGET_COMPANY
            else (
                f"Suggest up to {max_terms} NEW search queries to find more relevant "
                "competitors, partners, ad surfaces, or topic variants mentioned in the sources. "
                "Use concrete product, community, or divination/AR/nonprofit terms from the pages. "
                "Do NOT invent emails, person names, or URLs. Skip news headlines, "
                "product recalls, sports, and weather — they are off-topic.\n"
            )
        )
        + 'Respond with JSON only: {"terms": ["query one", "query two"]}'
    )
    try:
        response = chat_completions(
            {
                "model": "crm",
                "messages": [
                    {
                        "role": "system",
                        "content": "You output JSON only." + UNTRUSTED_DATA_SYSTEM_SUFFIX,
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 240,
            },
            timeout=120.0,
            actor=ACTOR,
            task=f"research follow-ups for {query[:40]}",
        )
        content = _extract_chat_content(response) or ""
    except Exception as exc:  # noqa: BLE001
        errors.append(f"LLM follow-up terms failed for {query!r}: {exc}")
        return []

    payload = extract_json_object(content)
    if not payload:
        return []
    terms = payload.get("terms") or payload.get("queries") or []
    cleaned: list[str] = []
    for term in terms:
        if isinstance(term, str) and term.strip():
            cleaned.append(term.strip()[:200])
        if len(cleaned) >= max_terms:
            break
    return cleaned


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
    elif kind == ResearchFindingKind.TARGET_COMPANY:
        prefix = f"Retail / F&B target company for {brand_label}: "
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
    brand_context = brand_context_for(ACTOR, brand)
    page_block = (
        f"{wrap_untrusted('url', hit.url, max_chars=500)}\n"
        f"{wrap_untrusted('title', page.title or hit.title, max_chars=300)}\n"
        f"{wrap_untrusted('snippet', hit.snippet, max_chars=500)}\n"
        f"{wrap_untrusted('page_excerpt', page.markdown, max_chars=3500)}"
    )
    if kind == ResearchFindingKind.COMPETITOR:
        competitor_guidance = research_competitor_guidance()
        system = (
            "You analyze competitor websites for a CRM research agent. "
            "Summarize positioning, audience, and product angle vs the target brand. "
            "Be factual; do not invent contact details, stats, or testimonials.\n\n"
            f"{competitor_guidance}"
            + UNTRUSTED_DATA_SYSTEM_SUFFIX
        )
        if brand_context:
            system += f"\n\n--- brand context (excerpt) ---\n{brand_context}"
        user = (
            f"Target brand: {brand_label}\n"
            f"{page_block}\n\n"
            'Return JSON: {"summary": "...", "why_it_matters": "..."}'
        )
        max_tokens = 320
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
        max_tokens = 320
    elif kind == ResearchFindingKind.AD_PLACEMENT:
        system = (
            "You analyze websites, forums, newsletters, podcasts, and communities that sell ads, "
            "take sponsorships, or offer promo/sticky/banner/board placement. "
            "Discovery only — do not invent pricing or contact emails. "
            "Assess brand fit and brand safety honestly (imageboards like 4chan often warrant caution).\n\n"
            f"{research_ad_placement_guidance()}"
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
        max_tokens = 320
    elif kind == ResearchFindingKind.TARGET_COMPANY:
        system = (
            "You extract named retail, grocery, food & beverage, CPG, restaurant, "
            "and convenience-store companies as CRM hunt targets. "
            "Prefer companies that appear to do more than $10 million in annual revenue. "
            "Never invent company names that are not in the source. "
            "Skip agencies, XR studios, software vendors, and listicle publishers."
            + UNTRUSTED_DATA_SYSTEM_SUFFIX
        )
        if brand_context:
            system += f"\n\n--- brand context (excerpt) ---\n{brand_context}"
        user = (
            f"Target brand: {brand_label}\n"
            f"{page_block}\n\n"
            "Return JSON: "
            '{"summary":"...","companies":[{"name":"Kroger","sector":"grocery",'
            '"revenue_hint":"$150B or unknown","why_target":"large US grocer"}]}. '
            "Include up to 25 companies. If the page is a single company site, "
            "return one company."
        )
        max_tokens = 900
    else:
        system = "You write concise CRM research summaries. Be factual." + UNTRUSTED_DATA_SYSTEM_SUFFIX
        user = page_block
        max_tokens = 320

    try:
        response = chat_completions(
            {
                "model": "crm",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
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

    if kind == ResearchFindingKind.TARGET_COMPANY:
        return _heuristic_target_company_extra(page, hit)

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


def _heuristic_target_company_extra(
    page: ScrapeResult,
    hit: SearchResult,
) -> dict[str, Any] | None:
    companies = heuristic_companies_from_title(page.title or hit.title)
    if not companies:
        return None
    extra: dict[str, Any] = {"companies": companies}
    if len(companies) == 1:
        extra["org_name"] = companies[0]["name"]
    return extra


def _ensure_target_company_extra(
    extra: dict[str, Any] | None,
    *,
    title: str,
) -> dict[str, Any]:
    merged = dict(extra or {})
    companies = companies_from_payload(merged)
    if not companies:
        companies = heuristic_companies_from_title(title)
    if companies:
        merged["companies"] = companies
        if len(companies) == 1:
            merged["org_name"] = companies[0]["name"]
    return merged


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

    classification = classify_resource_detailed(hit.url, page.title or hit.title, text)
    if classification.kind in ENGAGEMENT_VENUE_KINDS:
        extra["engagement_surface"] = True
        extra["ad_product"] = extra.get("ad_product") or "other"

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

    if kind == ResearchFindingKind.TARGET_COMPANY:
        companies = companies_from_payload(parsed)
        if not companies:
            companies = heuristic_companies_from_title(page.title or hit.title)
        if companies:
            extra["companies"] = companies
            if len(companies) == 1:
                extra["org_name"] = companies[0]["name"]

    return extra or None


def _parse_json_object(content: str) -> dict[str, Any] | None:
    from agent_crm.llm_text import extract_json_object

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
            "companies",
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
    from agent_crm.db import session_scope
    from agent_crm.models import Account
    from .utils import canonical_url

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


def _catalog_engagement_venue(
    *,
    url: str,
    title: str,
    brand: Brand,
    query: str,
    snippet: str | None,
    extra: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Persist forum/community ad-placement hits into the hunter venue catalog."""
    classification = classify_resource_detailed(url, title, snippet)
    if classification.kind not in ENGAGEMENT_VENUE_KINDS:
        return extra
    HuntStore().upsert_resource(
        url=url,
        brand=brand,
        title=title,
        found_via_query=query,
        snippet=(snippet or "")[:500] or None,
        kind=classification.kind,
    )
    merged = dict(extra or {})
    merged["engagement_surface"] = True
    return merged
