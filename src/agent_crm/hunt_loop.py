"""Bounded branching hunt loop: queue, param rotation, resource collection."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from .comment_people_store import process_scraped_page_comment_people
from .config import Settings, get_settings
from .contact_store import ContactExtractionBudget, process_scraped_page_contacts
from .engagement import (
    extract_engagement_signals,
    is_thread_url,
    venue_url_from_thread,
)
from .engagement_store import upsert_thread
from .enums import AgentStatus, Brand, ContactAudience, TopicalRelevanceVerdict
from .firecrawl_client import FirecrawlError, scrape
from .heartbeat import record_heartbeat
from .hunt_feedback import (
    HuntFeedbackBudget,
    enqueue_community_terms,
    enqueue_engagement_terms,
    enqueue_handle_terms,
    enqueue_person_terms,
)
from .hunt_relevance import assess_topical_relevance, is_obvious_off_topic_url
from .hunt_seeds import audience_from_origin, seed_query_entries
from .hunt_store import HuntStore
from .hunt_utils import (
    ResourceClassification,
    extract_heuristic_terms,
    is_junk_title,
)
from .job_store import enqueue_topical_relevance_job
from .llm_client import chat_completions
from .llm_text import UNTRUSTED_DATA_SYSTEM_SUFFIX, extract_json_object, wrap_untrusted
from .searxng_client import SearchResult, search
from .topic_relevance_store import upsert_url_topic_relevance

ACTOR = "outbound_hunter"

logger = logging.getLogger(__name__)

PARAM_PALETTES: list[dict | None] = [
    None,
    {"categories": "general", "pageno": 1},
    {"categories": "social media"},
    {"categories": "news", "time_range": "year"},
    {"categories": "it"},
]


@dataclass
class HuntBudget:
    max_queries: int = 0
    max_minutes: int | None = 0
    max_pages_per_query: int = 50

    def __post_init__(self) -> None:
        settings = get_settings()
        self.max_pages_per_query = min(self.max_pages_per_query, settings.hunter_max_pages_per_run)


@dataclass
class HuntLoopResult:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    queries_run: int = 0
    resources_found: int = 0
    branch_terms_enqueued: int = 0
    community_terms_enqueued: int = 0
    person_terms_enqueued: int = 0
    handle_terms_enqueued: int = 0
    engagement_terms_enqueued: int = 0
    stop_reason: str = "queue_empty"


def _wall_clock_deadline(max_minutes: int | None) -> float | None:
    """Return a monotonic deadline when max_minutes > 0; None means unlimited."""
    if max_minutes is None or max_minutes <= 0:
        return None
    return time.monotonic() + max_minutes * 60


def _query_budget_exhausted(queries_run: int, max_queries: int | None) -> bool:
    """Return True when the query budget is exhausted; 0/None means unlimited."""
    if max_queries is None or max_queries <= 0:
        return False
    return queries_run >= max_queries


def run_hunt_loop(
    *,
    query: str | None = None,
    brand: Brand = Brand.UNASSIGNED,
    budget: HuntBudget | None = None,
    resume: bool = True,
    summarize_branches: bool = True,
    searx_client: httpx.Client | None = None,
    firecrawl_client: httpx.Client | None = None,
) -> HuntLoopResult:
    """Run a bounded branching loop that grows the resource collection."""
    settings = get_settings()
    budget = budget or HuntBudget(
        max_queries=settings.hunter_max_queries_default,
        max_minutes=settings.hunter_max_minutes_default,
        max_pages_per_query=settings.hunter_max_pages_per_run,
    )
    store = HuntStore()
    result = HuntLoopResult()
    deadline = _wall_clock_deadline(budget.max_minutes)
    # This process is the only hunt-loop worker; reclaim rows left RUNNING
    # by a previous container restart instead of waiting 30 minutes.
    store.reset_stale_running_queries(stale_minutes=0)

    pending_existing = store.count_pending(brand if brand != Brand.UNASSIGNED else None)

    if query:
        store.enqueue_query(query=query, brand=brand, origin="seed", run_id=result.run_id)

    if brand != Brand.UNASSIGNED:
        for seed_query, seed_origin in seed_query_entries(brand):
            store.enqueue_query(
                query=seed_query,
                brand=brand,
                origin=seed_origin,
                run_id=result.run_id,
            )
    elif not query and pending_existing == 0:
        result.stop_reason = "no_seed"
        record_heartbeat(ACTOR, status=AgentStatus.IDLE)
        return result

    use_run_id = None if resume else result.run_id
    brand_filter = brand if brand != Brand.UNASSIGNED else None
    palette_index = 0
    feedback_budget = HuntFeedbackBudget.from_settings()

    while not _query_budget_exhausted(result.queries_run, budget.max_queries) and (
        deadline is None or time.monotonic() < deadline
    ):
        contact_budget = ContactExtractionBudget.from_settings()
        pending = store.claim_next_pending_query(run_id=use_run_id, brand=brand_filter)
        if pending is None:
            result.stop_reason = "queue_empty"
            break

        params = (
            pending.params
            if pending.params is not None
            else PARAM_PALETTES[palette_index % len(PARAM_PALETTES)] or {}
        )
        palette_index += 1

        query_progress = (
            f"query {result.queries_run + 1}/{budget.max_queries}"
            if budget.max_queries > 0
            else f"query {result.queries_run + 1}"
        )
        store.set_heartbeat(
            AgentStatus.THINKING,
            f"{query_progress}: {pending.query}",
            resource=settings.searxng_url,
        )

        try:
            query_audience = audience_from_origin(pending.origin)
            stats = _run_queued_query(
                pending.query,
                brand=pending.brand,
                params=params,
                max_pages=budget.max_pages_per_query,
                origin=pending.origin,
                run_id=pending.run_id or result.run_id,
                store=store,
                settings=settings,
                summarize_branches=summarize_branches,
                searx_client=searx_client,
                firecrawl_client=firecrawl_client,
                contact_budget=contact_budget,
                feedback_budget=feedback_budget,
                audience=query_audience,
            )
        except Exception as exc:  # noqa: BLE001
            from .enums import ImprovementSourceAgent
            from .orchestrator import note_worker_failure

            note_worker_failure(
                source_agent=ImprovementSourceAgent.HUNT_LOOP,
                error_text=str(exc),
                context=f"hunt query {pending.id}",
            )
            store.mark_query_failed(pending.id, str(exc))
            continue

        result.queries_run += 1
        result.resources_found += stats["resources_collected"]
        result.branch_terms_enqueued += stats["branch_terms_enqueued"]
        result.community_terms_enqueued += stats["community_terms_enqueued"]
        result.person_terms_enqueued += stats["person_terms_enqueued"]
        result.handle_terms_enqueued += stats["handle_terms_enqueued"]
        result.engagement_terms_enqueued += stats["engagement_terms_enqueued"]
        store.mark_query_completed(pending.id)

        if deadline is not None and time.monotonic() >= deadline:
            result.stop_reason = "max_minutes"
            break
        if _query_budget_exhausted(result.queries_run, budget.max_queries):
            result.stop_reason = "max_queries"
            break

    if (
        result.stop_reason == "queue_empty"
        and budget.max_queries > 0
        and _query_budget_exhausted(result.queries_run, budget.max_queries)
    ):
        result.stop_reason = "max_queries"

    store.set_heartbeat(
        AgentStatus.IDLE,
        f"loop stopped ({result.stop_reason}): {result.queries_run} queries, "
        f"{result.resources_found} resources",
    )
    return result


def _run_queued_query(
    query: str,
    *,
    brand: Brand,
    params: dict[str, Any],
    max_pages: int,
    origin: str,
    run_id: str | None,
    store: HuntStore,
    settings: Settings,
    summarize_branches: bool,
    searx_client: httpx.Client | None,
    firecrawl_client: httpx.Client | None,
    contact_budget: ContactExtractionBudget | None = None,
    feedback_budget: HuntFeedbackBudget | None = None,
    audience: ContactAudience | None = None,
) -> dict[str, int]:
    search_kwargs = dict(params)
    results = search(
        query,
        limit=settings.hunter_search_result_limit,
        client=searx_client,
        **search_kwargs,
    )
    results = _filter_relevant_hunt_results(
        results,
        brand=brand,
        query=query,
    )
    result_dicts = [
        {"title": hit.title, "url": hit.url, "content": hit.snippet} for hit in results
    ]

    feedback_budget = feedback_budget or HuntFeedbackBudget.from_settings()
    contact_budget = contact_budget or ContactExtractionBudget.from_settings()
    resources, community_terms, person_terms, engagement_terms = _collect_from_results(
        store,
        query,
        brand,
        results,
        run_id=run_id,
        feedback_budget=feedback_budget,
        audience=audience,
    )

    pages_scraped = 0
    handle_terms = 0
    if results and max_pages > 0:
        store.set_heartbeat(
            AgentStatus.WORKING,
            f"scraping for: {query}",
            resource=settings.firecrawl_url,
        )
        for hit in results:
            if pages_scraped >= max_pages:
                break
            if not _is_scrapable_url(hit.url):
                continue
            try:
                page = scrape(hit.url, client=firecrawl_client)
            except FirecrawlError:
                continue
            title = page.title or hit.title
            if is_junk_title(title):
                continue
            upsert = store.upsert_resource(
                url=hit.url,
                brand=brand,
                title=title,
                found_via_query=query,
                snippet=(page.markdown or hit.snippet or "")[:500] or None,
            )
            if upsert.resource is not None:
                pages_scraped += 1
                if upsert.is_new and upsert.classification is not None:
                    community_terms += enqueue_community_terms(
                        store,
                        classification=upsert.classification,
                        title=title,
                        brand=brand,
                        run_id=run_id,
                        budget=feedback_budget,
                        audience=audience,
                    )
                    engagement_terms += enqueue_engagement_terms(
                        store,
                        classification=upsert.classification,
                        url=hit.url,
                        brand=brand,
                        run_id=run_id,
                        budget=feedback_budget,
                        audience=audience,
                    )
                if upsert.classification is not None:
                    _catalog_thread(
                        url=hit.url,
                        brand=brand,
                        title=title,
                        snippet=page.markdown or hit.snippet,
                        classification=upsert.classification,
                        hunt_resource_id=upsert.resource.id,
                        found_via_query=query,
                    )
                try:
                    profiles = process_scraped_page_contacts(
                        markdown=page.markdown,
                        source_url=hit.url,
                        brand=brand,
                        searx_client=searx_client,
                        budget=contact_budget,
                        audience=audience,
                    )
                    for profile in profiles:
                        person_terms += enqueue_person_terms(
                            store,
                            name=profile.name or "",
                            brand=brand,
                            run_id=run_id,
                            budget=feedback_budget,
                            audience=audience,
                        )
                    comment_people = process_scraped_page_comment_people(
                        markdown=page.markdown,
                        html=getattr(page, "html", None),
                        source_url=hit.url,
                        brand=brand,
                        budget=contact_budget,
                        audience=audience,
                    )
                    for person in comment_people:
                        handle_terms += enqueue_handle_terms(
                            store,
                            platform=person.platform,
                            handle=person.handle,
                            display_name=person.display_name,
                            brand=brand,
                            run_id=run_id,
                            budget=feedback_budget,
                            audience=audience,
                        )
                except Exception:  # noqa: BLE001
                    pass
        resources += pages_scraped

    branch_terms = _extract_branch_terms(
        query,
        result_dicts,
        max_terms=settings.hunter_max_branch_terms,
        summarize=summarize_branches,
        brand=brand,
        audience=audience,
    )
    enqueued = 0
    for term in branch_terms:
        if store.enqueue_query(
            query=term,
            brand=brand,
            origin=f"branch:{origin}",
            params=None,
            run_id=run_id,
        ):
            enqueued += 1

    return {
        "resources_collected": resources,
        "branch_terms_enqueued": enqueued,
        "community_terms_enqueued": community_terms,
        "person_terms_enqueued": person_terms,
        "handle_terms_enqueued": handle_terms,
        "engagement_terms_enqueued": engagement_terms,
    }


def _collect_from_results(
    store: HuntStore,
    query: str,
    brand: Brand,
    results: list[SearchResult],
    *,
    run_id: str | None = None,
    feedback_budget: HuntFeedbackBudget | None = None,
    audience: ContactAudience | None = None,
) -> tuple[int, int, int, int]:
    feedback_budget = feedback_budget or HuntFeedbackBudget.from_settings()
    count = 0
    community_terms = 0
    person_terms = 0
    engagement_terms = 0
    for hit in results:
        upsert = store.upsert_resource(
            url=hit.url,
            brand=brand,
            title=hit.title,
            found_via_query=query,
            snippet=(hit.snippet or "")[:500] or None,
        )
        if upsert.resource is not None:
            count += 1
            if upsert.is_new and upsert.classification is not None:
                community_terms += enqueue_community_terms(
                    store,
                    classification=upsert.classification,
                    title=hit.title,
                    brand=brand,
                    run_id=run_id,
                    budget=feedback_budget,
                    audience=audience,
                )
                engagement_terms += enqueue_engagement_terms(
                    store,
                    classification=upsert.classification,
                    url=hit.url,
                    brand=brand,
                    run_id=run_id,
                    budget=feedback_budget,
                    audience=audience,
                )
            if upsert.classification is not None:
                _catalog_thread(
                    url=hit.url,
                    brand=brand,
                    title=hit.title,
                    snippet=hit.snippet,
                    classification=upsert.classification,
                    hunt_resource_id=upsert.resource.id,
                    found_via_query=query,
                )
    return count, community_terms, person_terms, engagement_terms


def _catalog_thread(
    *,
    url: str,
    brand: Brand,
    title: str | None,
    snippet: str | None,
    classification: ResourceClassification,
    hunt_resource_id: int | None,
    found_via_query: str,
) -> None:
    if not is_thread_url(url):
        return
    signals = extract_engagement_signals(
        title, snippet, kind=classification.kind
    )
    upsert_thread(
        url=url,
        brand=brand,
        title=title,
        signals=signals,
        hunt_resource_id=hunt_resource_id,
        platform=classification.platform,
        venue_url=venue_url_from_thread(url, classification),
        excerpt=(snippet or "")[:800] or None,
        found_via_query=found_via_query,
    )


def _extract_branch_terms(
    query: str,
    results: list[dict[str, Any]],
    *,
    max_terms: int,
    summarize: bool,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
) -> list[str]:
    heuristic = extract_heuristic_terms(results, max_terms=max_terms)
    if not summarize:
        return heuristic

    llm_terms = _llm_branch_terms(
        query,
        results,
        max_terms=max_terms,
        brand=brand,
        audience=audience,
    )
    merged: list[str] = []
    seen: set[str] = set()
    for term in heuristic + llm_terms:
        key = HuntStore.normalize_term(term)
        if key in seen or key == HuntStore.normalize_term(query):
            continue
        seen.add(key)
        merged.append(term)
        if len(merged) >= max_terms:
            break
    return merged


def _llm_branch_terms(
    query: str,
    results: list[dict[str, Any]],
    *,
    max_terms: int,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
) -> list[str]:
    lines = []
    for idx, item in enumerate(results[:12], start=1):
        lines.append(
            wrap_untrusted(
                f"hit_{idx}",
                f"{item.get('title', '')} | {item.get('url', '')} | "
                f"{(item.get('content') or '')[:200]}",
                max_chars=280,
            )
        )
    if brand == Brand.TACTIC_STUDIO and audience == ContactAudience.MARKETING:
        prompt = (
            "You help an outbound researcher find named marketing leaders at "
            "large retail and food & beverage companies (more than $10 million "
            "annual revenue).\n"
            f"Original query: {wrap_untrusted('query', query, max_chars=300)}\n"
            "Search results:\n"
            + "\n".join(lines)
            + "\n\n"
            f"Suggest up to {max_terms} NEW search queries for VP of marketing, "
            "brand managers, marketing managers, and brand-management leadership "
            "directories, team pages, and press bios at those companies. "
            "Prefer company about/leadership pages over XR communities. "
            "Do NOT invent emails or person names.\n"
            'Respond with JSON only: {"terms": ["query one", "query two"]}'
        )
    else:
        prompt = (
            "You help an outbound researcher find online communities, directories, "
            "newsletters, forums, and listicles where potential users gather.\n"
            f"Original query: {wrap_untrusted('query', query, max_chars=300)}\n"
            "Search results:\n"
            + "\n".join(lines)
            + "\n\n"
            f"Suggest up to {max_terms} NEW search queries to find more such resources. "
            "Focus on communities, directories, newsletters, forums — not individual people. "
            "Do NOT invent emails or person names.\n"
            'Respond with JSON only: {"terms": ["query one", "query two"]}'
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
            },
            timeout=120.0,
            actor=ACTOR,
            task=f"branch terms for {query[:40]}",
        )
        content = response["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        return []

    payload = extract_json_object(content)
    if not payload:
        return []
    terms = payload.get("terms") or payload.get("queries") or []
    cleaned: list[str] = []
    for term in terms:
        if isinstance(term, str) and term.strip():
            cleaned.append(term.strip())
        if len(cleaned) >= max_terms:
            break
    return cleaned


def _filter_relevant_hunt_results(
    results: list[SearchResult],
    *,
    brand: Brand,
    query: str,
) -> list[SearchResult]:
    if brand == Brand.UNASSIGNED:
        return results
    kept: list[SearchResult] = []
    for hit in results:
        assessment = assess_topical_relevance(
            brand=brand,
            url=hit.url,
            title=hit.title,
            snippet=hit.snippet,
            query=query,
            allow_spark=is_obvious_off_topic_url(hit.url) is None,
        )
        if assessment.verdict == TopicalRelevanceVerdict.OFF_TOPIC:
            upsert_url_topic_relevance(
                url=hit.url,
                brand=brand,
                assessment=assessment,
                source_kind="hunt_serp",
            )
            continue
        if assessment.verdict == TopicalRelevanceVerdict.UNCERTAIN:
            upsert_url_topic_relevance(
                url=hit.url,
                brand=brand,
                assessment=assessment,
                source_kind="hunt_serp",
            )
            # Defer scrape until a deeper topical job confirms on-topic.
            enqueue_topical_relevance_job(
                url=hit.url,
                brand=brand,
                source_kind="hunt_serp_uncertain",
                query=query,
            )
            continue
        if assessment.verdict == TopicalRelevanceVerdict.ON_TOPIC:
            upsert_url_topic_relevance(
                url=hit.url,
                brand=brand,
                assessment=assessment,
                source_kind="hunt_serp",
            )
            kept.append(hit)
    return kept


def _is_scrapable_url(url: str) -> bool:
    from .url_safety import is_public_http_url

    return is_public_http_url(url, resolve_dns=False)
