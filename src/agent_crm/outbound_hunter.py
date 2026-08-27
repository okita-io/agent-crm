"""Outbound Hunter: SearXNG search + Firecrawl scrape + Lead creation.

Failures are recorded as notes on the hunt (or per-lead activities) and never
block the pipeline — same best-effort contract as enrichment.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import get_settings
from .comment_people_store import process_scraped_page_comment_people
from .contact_store import ContactExtractionBudget, process_scraped_page_contacts
from .enums import ActivityType, AgentStatus, Brand, LeadSource, Stage, TopicalRelevanceVerdict
from .firecrawl_client import FirecrawlError, ScrapeResult, scrape
from .llm_client import chat_completions
from .llm_text import UNTRUSTED_DATA_SYSTEM_SUFFIX, wrap_untrusted
from .hunt_relevance import assess_topical_relevance, is_obvious_off_topic_url
from .job_store import enqueue_topical_relevance_job
from .pipeline import PipelineManager
from .schemas import EnrichmentInput, HuntRequest, HuntResult, LeadCreate, LeadOut
from .searxng_client import SearchResult, SearxngError, search
from .topic_relevance_store import upsert_url_topic_relevance

from .tooling import CRMToolkit

ACTOR = "outbound_hunter"


def run_hunt(
    request: HuntRequest,
    *,
    searx_client: httpx.Client | None = None,
    firecrawl_client: httpx.Client | None = None,
) -> HuntResult:
    """Execute one bounded hunt: search, scrape, optionally summarize, write leads."""
    settings = get_settings()
    crm = CRMToolkit(actor=ACTOR)
    pm = PipelineManager(actor=ACTOR)

    page_cap = min(request.max_pages, settings.hunter_max_pages_per_run)
    search_cap = min(request.search_limit, settings.hunter_search_result_limit)

    errors: list[str] = []
    leads_created: list[int] = []
    contact_budget = ContactExtractionBudget.from_settings()

    crm.record_heartbeat(
        status=AgentStatus.THINKING,
        task=f"searching: {request.query}",
        resource=settings.searxng_url,
    )

    try:
        results = search(
            request.query,
            limit=search_cap,
            client=searx_client,
        )
    except SearxngError as exc:
        crm.record_heartbeat(status=AgentStatus.IDLE)
        crm.log_note(
            f"SearXNG search failed: {exc}",
            type=ActivityType.ERROR,
            payload={"query": request.query},
        )
        return HuntResult(
            query=request.query,
            brand=request.brand,
            search_results=0,
            scraped=0,
            leads_created=[],
            errors=[str(exc)],
        )

    crm.log_note(
        f"Hunt started for {request.query!r} ({len(results)} search hits)",
        type=ActivityType.SCRAPE,
        payload={"query": request.query, "search_hits": len(results)},
    )

    scraped_count = 0
    brand = request.brand or Brand.UNASSIGNED
    for hit in results:
        if scraped_count >= page_cap:
            break
        if not _is_scrapable_url(hit.url):
            continue

        if brand != Brand.UNASSIGNED:
            assessment = assess_topical_relevance(
                brand=brand,
                url=hit.url,
                title=hit.title,
                snippet=hit.snippet,
                query=request.query,
                allow_spark=is_obvious_off_topic_url(hit.url) is None,
            )
            upsert_url_topic_relevance(
                url=hit.url,
                brand=brand,
                assessment=assessment,
                source_kind="hunt_run",
            )
            if assessment.verdict == TopicalRelevanceVerdict.OFF_TOPIC:
                continue
            if assessment.verdict == TopicalRelevanceVerdict.UNCERTAIN:
                enqueue_topical_relevance_job(
                    url=hit.url,
                    brand=brand,
                    source_kind="hunt_run_uncertain",
                    query=request.query,
                )
                continue

        crm.record_heartbeat(
            status=AgentStatus.WORKING,
            task=f"scraping {hit.url}",
            resource=settings.firecrawl_url,
        )

        try:
            page = scrape(hit.url, client=firecrawl_client)
        except FirecrawlError as exc:
            message = f"Firecrawl scrape failed for {hit.url}: {exc}"
            errors.append(message)
            crm.log_note(message, type=ActivityType.ERROR, payload={"url": hit.url})
            continue

        scraped_count += 1
        summary = _fallback_summary(hit, page)
        if request.summarize:
            crm.record_heartbeat(
                status=AgentStatus.WORKING,
                task=f"summarizing {hit.url}",
                resource=f"Spark queue ({settings.llm_base_url})",
            )
            summary = _maybe_summarize(hit, page, summary, errors=errors)

        lead = _create_lead_from_hit(
            crm,
            hit=hit,
            page=page,
            summary=summary,
            brand=request.brand,
            query=request.query,
        )
        leads_created.append(lead.id)

        if request.transition_to_prospect:
            try:
                pm.transition(lead.id, to_stage=Stage.PROSPECT)
            except Exception as exc:  # noqa: BLE001 — best-effort stage move
                errors.append(f"Could not move lead {lead.id} to prospect: {exc}")

        crm.record_enrichment(
            lead.id,
            EnrichmentInput(
                summary=summary,
                website=hit.url,
            ),
        )

        try:
            process_scraped_page_contacts(
                markdown=page.markdown,
                source_url=hit.url,
                brand=request.brand or Brand.UNASSIGNED,
                searx_client=searx_client,
                budget=contact_budget,
            )
            process_scraped_page_comment_people(
                markdown=page.markdown,
                source_url=hit.url,
                brand=request.brand or Brand.UNASSIGNED,
                budget=contact_budget,
            )
        except Exception:  # noqa: BLE001
            pass

    crm.record_heartbeat(status=AgentStatus.IDLE)
    return HuntResult(
        query=request.query,
        brand=request.brand,
        search_results=len(results),
        scraped=scraped_count,
        leads_created=leads_created,
        errors=errors,
    )

def _is_scrapable_url(url: str) -> bool:
    from .url_safety import is_public_http_url

    return is_public_http_url(url, resolve_dns=False)


def _fallback_summary(hit: SearchResult, page: ScrapeResult) -> str:
    title = page.title or hit.title or hit.url
    snippet = hit.snippet
    body = (page.markdown or "").strip()
    if body:
        body = body[:1200]
        return f"{title}\n\n{snippet}\n\n{body}".strip()
    return f"{title}\n\n{snippet}".strip()


def _maybe_summarize(
    hit: SearchResult,
    page: ScrapeResult,
    fallback: str,
    *,
    errors: list[str],
) -> str:
    prompt = (
        "Summarize this prospect in 2-3 sentences for a CRM lead record. "
        "Focus on who they are, what they do, and why they might need creative/tech services. "
        "Be factual; do not invent contact details.\n\n"
        f"{wrap_untrusted('title', page.title or hit.title, max_chars=300)}\n"
        f"{wrap_untrusted('url', hit.url, max_chars=500)}\n"
        f"{wrap_untrusted('snippet', hit.snippet, max_chars=500)}\n"
        f"{wrap_untrusted('page_excerpt', page.markdown, max_chars=3000)}"
    )
    try:
        response = chat_completions(
            {
                "model": "crm",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You write concise CRM prospect summaries."
                            + UNTRUSTED_DATA_SYSTEM_SUFFIX
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 220,
                "temperature": 0.2,
            },
            timeout=120.0,
            actor=ACTOR,
            task=f"summarize {hit.url}",
        )
        content = _extract_chat_content(response)
        if content:
            return content.strip()
    except Exception as exc:  # noqa: BLE001 — enrichment must not block pipeline
        message = f"LLM summary failed for {hit.url}: {exc}"
        errors.append(message)
    return fallback


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


def _create_lead_from_hit(
    crm: CRMToolkit,
    *,
    hit: SearchResult,
    page: ScrapeResult,
    summary: str,
    brand: Brand | None,
    query: str,
) -> LeadOut:
    company = _guess_company(hit, page)
    name = _guess_name(hit, page, company)
    payload = {
        "hunt_query": query,
        "search_title": hit.title,
        "search_snippet": hit.snippet,
        "url": hit.url,
        "page_title": page.title,
        "metadata": page.metadata,
    }
    lead = crm.create_lead(
        LeadCreate(
            name=name,
            company=company,
            source=LeadSource.HUNTER,
            raw_payload=payload,
        )
    )
    if brand is not None and brand != Brand.UNASSIGNED:
        crm.route_brand(lead.id, brand)
    from .job_store import enqueue_verify_lead_job

    enqueue_verify_lead_job(lead.id)
    return lead


def _guess_company(hit: SearchResult, page: ScrapeResult) -> str | None:
    for candidate in (
        page.metadata.get("og:site_name") if isinstance(page.metadata, dict) else None,
        page.title,
        hit.title,
    ):
        if isinstance(candidate, str) and candidate.strip():
            cleaned = _clean_title(candidate)
            if cleaned:
                return cleaned
    domain = urlparse(hit.url).netloc
    domain = domain.removeprefix("www.")
    return domain or None


def _guess_name(hit: SearchResult, page: ScrapeResult, company: str | None) -> str | None:
    if company:
        return company
    if hit.title:
        return _clean_title(hit.title)
    return urlparse(hit.url).netloc or None


def _clean_title(value: str) -> str:
    text = value.strip()
    text = re.split(r"\s*[|\-–—]\s*", text, maxsplit=1)[0].strip()
    return text[:200] if text else ""
