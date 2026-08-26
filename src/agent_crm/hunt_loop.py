"""Bounded branching hunt loop: queue, param rotation, resource collection."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import Settings, get_settings
from .contact_store import ContactExtractionBudget, process_scraped_page_contacts
from .enums import AgentStatus, Brand, HuntQueryStatus
from .firecrawl_client import FirecrawlError, scrape
from .heartbeat import record_heartbeat
from .hunt_feedback import (
    HuntFeedbackBudget,
    enqueue_community_terms,
    enqueue_person_terms,
)
from .hunt_seeds import seeds_for_brand
from .hunt_store import HuntStore
from .hunt_utils import extract_heuristic_terms, is_junk_title
from .llm_client import chat_completions
from .searxng_client import SearchResult, search

ACTOR = "outbound_hunter"

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

    pending_existing = store.count_pending(brand if brand != Brand.UNASSIGNED else None)
    if resume and not query and pending_existing > 0:
        should_seed = False
    else:
        should_seed = bool(query) or brand != Brand.UNASSIGNED

    if should_seed:
        if query:
            store.enqueue_query(query=query, brand=brand, origin="seed", run_id=result.run_id)
        elif brand != Brand.UNASSIGNED:
            for seed in seeds_for_brand(brand):
                store.enqueue_query(query=seed, brand=brand, origin="seed_pack", run_id=result.run_id)
        else:
            result.stop_reason = "no_seed"
            record_heartbeat(ACTOR, status=AgentStatus.IDLE)
            return result

    use_run_id = None if resume else result.run_id
    brand_filter = brand if brand != Brand.UNASSIGNED else None
    palette_index = 0
    contact_budget = ContactExtractionBudget.from_settings()
    feedback_budget = HuntFeedbackBudget.from_settings()

    while not _query_budget_exhausted(result.queries_run, budget.max_queries) and (
        deadline is None or time.monotonic() < deadline
    ):
        pending = store.next_pending_query(run_id=use_run_id, brand=brand_filter)
        if pending is None:
            result.stop_reason = "queue_empty"
            break
        if pending.status == HuntQueryStatus.COMPLETED:
            continue

        params = (
            pending.params
            if pending.params is not None
            else PARAM_PALETTES[palette_index % len(PARAM_PALETTES)] or {}
        )
        palette_index += 1

        store.mark_query_running(pending.id)
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
            )
        except Exception as exc:  # noqa: BLE001
            store.mark_query_failed(pending.id, str(exc))
            continue

        result.queries_run += 1
        result.resources_found += stats["resources_collected"]
        result.branch_terms_enqueued += stats["branch_terms_enqueued"]
        result.community_terms_enqueued += stats["community_terms_enqueued"]
        result.person_terms_enqueued += stats["person_terms_enqueued"]
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
) -> dict[str, int]:
    search_kwargs = dict(params)
    results = search(
        query,
        limit=settings.hunter_search_result_limit,
        client=searx_client,
        **search_kwargs,
    )
    result_dicts = [
        {"title": hit.title, "url": hit.url, "content": hit.snippet} for hit in results
    ]

    feedback_budget = feedback_budget or HuntFeedbackBudget.from_settings()
    resources, community_terms, person_terms = _collect_from_results(
        store,
        query,
        brand,
        results,
        run_id=run_id,
        feedback_budget=feedback_budget,
    )

    pages_scraped = 0
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
                    )
                try:
                    profiles = process_scraped_page_contacts(
                        markdown=page.markdown,
                        source_url=hit.url,
                        brand=brand,
                        searx_client=searx_client,
                        budget=contact_budget,
                    )
                    for profile in profiles:
                        person_terms += enqueue_person_terms(
                            store,
                            name=profile.name or "",
                            brand=brand,
                            run_id=run_id,
                            budget=feedback_budget,
                        )
                except Exception:  # noqa: BLE001
                    pass
        resources += pages_scraped

    branch_terms = _extract_branch_terms(
        query,
        result_dicts,
        max_terms=settings.hunter_max_branch_terms,
        summarize=summarize_branches,
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
    }


def _collect_from_results(
    store: HuntStore,
    query: str,
    brand: Brand,
    results: list[SearchResult],
    *,
    run_id: str | None = None,
    feedback_budget: HuntFeedbackBudget | None = None,
) -> tuple[int, int, int]:
    feedback_budget = feedback_budget or HuntFeedbackBudget.from_settings()
    count = 0
    community_terms = 0
    person_terms = 0
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
                )
    return count, community_terms, person_terms


def _extract_branch_terms(
    query: str,
    results: list[dict[str, Any]],
    *,
    max_terms: int,
    summarize: bool,
) -> list[str]:
    heuristic = extract_heuristic_terms(results, max_terms=max_terms)
    if not summarize:
        return heuristic

    llm_terms = _llm_branch_terms(query, results, max_terms=max_terms)
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


def _llm_branch_terms(query: str, results: list[dict[str, Any]], *, max_terms: int) -> list[str]:
    lines = []
    for idx, item in enumerate(results[:12], start=1):
        lines.append(
            f"{idx}. {item.get('title', '')} | {item.get('url', '')} | "
            f"{(item.get('content') or '')[:200]}"
        )
    prompt = (
        "You help an outbound researcher find online communities, directories, "
        "newsletters, forums, and listicles where potential users gather.\n"
        f"Original query: {query}\n"
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
                    {"role": "system", "content": "You output JSON only."},
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

    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return []
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    terms = payload.get("terms") or payload.get("queries") or []
    cleaned: list[str] = []
    for term in terms:
        if isinstance(term, str) and term.strip():
            cleaned.append(term.strip())
        if len(cleaned) >= max_terms:
            break
    return cleaned


def _is_scrapable_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
