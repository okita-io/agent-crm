"""Agent engagement loop: drain an append-only query queue and draft comment replies.

Discovery only. This agent never publishes — the publisher worker sends after
a human schedules a publish_job.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from agent_crm.agent_control import stop_if_disabled, wait_while_disabled
from agent_crm.config import get_settings
from .runner import (
    extract_engagement_signals,
    is_engagement_venue,
    is_thread_url,
    venue_scan_queries,
    venue_url_from_thread,
)
from .feedback import extract_engagement_follow_up_terms
from .query_store import EngagementQueryStore
from .store import (
    has_draft,
    list_engagement_venues,
    mark_thread_draft_ready,
    mark_thread_scanned,
    mark_venue_scanned,
    upsert_draft,
    upsert_thread,
)
from agent_crm.enums import ActivityType, AgentStatus, Brand, ImprovementSourceAgent
from agent_crm.firecrawl_client import FirecrawlError, scrape
from agent_crm.heartbeat import record_heartbeat
from agent_crm.hunt.store import HuntStore
from agent_crm.hunt.utils import classify_resource_detailed, is_junk_title
from agent_crm.llm_client import chat_completions
from agent_crm.llm_text import UNTRUSTED_DATA_SYSTEM_SUFFIX, extract_json_object, wrap_untrusted
from agent_crm.models import EngagementThread, HuntResource
from agent_crm.searxng_client import SearxngError, search
from agent_crm.skill_runtime import brand_context_for, has_skill
from agent_crm.social_skill import engagement_draft_guidance
from agent_crm.tooling import CRMToolkit
from agent_crm.url_safety import is_public_http_url

ACTOR = "engagement"
WATCH_POLL_SECONDS = 60.0

ENGAGEMENT_LOOP_BRANDS: tuple[Brand, ...] = (
    Brand.CELESTIAL_NEXUS,
    Brand.MIDNIGHTSATIN,
    Brand.HEYBUDDY,
    Brand.TACTIC_STUDIO,
)


def engagement_loop_brands() -> tuple[Brand, ...]:
    from agent_crm.projects.channel_flags import active_brands_for

    return active_brands_for("engage") or ENGAGEMENT_LOOP_BRANDS


@dataclass
class EngagementBudget:
    max_venues: int = 10
    max_pages_per_venue: int = 15
    max_minutes: int = 45

    def __post_init__(self) -> None:
        settings = get_settings()
        self.max_venues = min(self.max_venues, settings.engagement_max_venues_per_run)
        self.max_pages_per_venue = min(
            self.max_pages_per_venue, settings.engagement_max_pages_per_venue
        )


@dataclass
class EngagementLoopResult:
    venues_scanned: int = 0
    threads_cataloged: int = 0
    drafts_written: int = 0
    pages_scraped: int = 0
    follow_up_terms_enqueued: int = 0
    errors: list[str] = field(default_factory=list)
    stop_reason: str = "queue_empty"


def _seed_engagement_queue(
    store: EngagementQueryStore, *, brand: Brand | None
) -> None:
    venues = list_engagement_venues(brand=brand, limit=200)
    for venue in venues:
        classification = classify_resource_detailed(venue.url, venue.title, venue.notes)
        queries = venue_scan_queries(classification, url=venue.url)
        if not queries:
            queries = [f"site:{venue.domain} popular threads"]
        origin = f"venue:{venue.domain}"[:128]
        for query in queries:
            store.enqueue_query(
                query=query,
                brand=venue.brand,
                origin=origin,
                hunt_resource_id=venue.id,
            )


def run_engagement_loop(
    *,
    brand: Brand | None = None,
    budget: EngagementBudget | None = None,
    summarize: bool = True,
    searx_client: httpx.Client | None = None,
    firecrawl_client: httpx.Client | None = None,
) -> EngagementLoopResult:
    """Drain the append-only engagement queue and draft replies (does not publish)."""
    settings = get_settings()
    budget = budget or EngagementBudget(
        max_venues=settings.engagement_max_venues_per_run,
        max_pages_per_venue=settings.engagement_max_pages_per_venue,
        max_minutes=settings.engagement_max_minutes_default,
    )
    crm = CRMToolkit(actor=ACTOR)
    result = EngagementLoopResult()
    if stop_if_disabled(ACTOR):
        result.stop_reason = "disabled"
        return result
    deadline = None if budget.max_minutes <= 0 else time.monotonic() + budget.max_minutes * 60
    store = EngagementQueryStore()
    store.reset_stale_running_queries(stale_minutes=0)
    _seed_engagement_queue(store, brand=brand)

    if store.count_pending(brand=brand) == 0:
        record_heartbeat(ACTOR, status=AgentStatus.IDLE, task="no venues due")
        result.stop_reason = "queue_empty"
        return result

    crm.log_note(
        f"Engagement scan started ({store.count_pending(brand=brand)} pending queries)",
        type=ActivityType.NOTE,
        payload={"brand": brand.value if brand else None},
    )

    scanned_venues: set[int] = set()
    queries_run = 0
    brand_cycle = 0
    idle_rounds = 0
    brands = (brand,) if brand is not None else engagement_loop_brands()

    while True:
        if stop_if_disabled(ACTOR):
            result.stop_reason = "disabled"
            break
        if deadline is not None and time.monotonic() >= deadline:
            result.stop_reason = "max_minutes"
            break
        if queries_run >= budget.max_venues:
            result.stop_reason = "max_venues"
            break

        cycle_brand = brands[brand_cycle % len(brands)]
        brand_cycle += 1
        claimed = store.claim_next_pending_query(brand=cycle_brand)
        if claimed is None:
            idle_rounds += 1
            if idle_rounds >= len(brands):
                result.stop_reason = "queue_empty"
                break
            continue

        idle_rounds = 0
        queries_run += 1
        venue = _load_venue(claimed.hunt_resource_id)
        record_heartbeat(
            ACTOR,
            status=AgentStatus.THINKING,
            task=f"searching: {claimed.query}",
            resource=settings.searxng_url,
        )
        try:
            stats = _run_engagement_query(
                query=claimed.query,
                brand=claimed.brand,
                venue=venue,
                budget=budget,
                summarize=summarize,
                searx_client=searx_client,
                firecrawl_client=firecrawl_client,
                queue=store,
            )
        except Exception as exc:  # noqa: BLE001
            from agent_crm.agency.orchestrator import note_worker_failure

            message = f"engagement scan failed for {claimed.query!r}: {exc}"
            result.errors.append(message)
            note_worker_failure(
                source_agent=ImprovementSourceAgent.ENGAGEMENT_LOOP,
                error_text=str(exc),
                context=f"query {claimed.id}",
            )
            store.mark_query_failed(claimed.id, message)
            continue

        result.threads_cataloged += stats["threads"]
        result.drafts_written += stats["drafts"]
        result.pages_scraped += stats["pages"]
        result.follow_up_terms_enqueued += stats["follow_ups"]
        store.mark_query_completed(claimed.id)
        if venue is not None and venue.id not in scanned_venues:
            scanned_venues.add(venue.id)
            mark_venue_scanned(venue.id, interval_hours=settings.engagement_scan_interval_hours)

    result.venues_scanned = len(scanned_venues) or queries_run
    record_heartbeat(
        ACTOR,
        status=AgentStatus.IDLE,
        task=(
            f"loop stopped ({result.stop_reason}): {result.venues_scanned} venues, "
            f"{result.threads_cataloged} threads, {result.drafts_written} drafts"
        ),
    )
    if result.stop_reason == "queue_empty" and (result.venues_scanned or queries_run):
        result.stop_reason = "complete"
    return result


def run_engagement_loop_watch(
    *,
    brand: Brand | None = None,
    budget: EngagementBudget | None = None,
    summarize: bool = True,
    searx_client: httpx.Client | None = None,
    firecrawl_client: httpx.Client | None = None,
) -> None:
    """Drain the engagement queue forever, sleeping when the backlog is empty."""
    store = EngagementQueryStore()
    while True:
        wait_while_disabled(ACTOR)
        run_engagement_loop(
            brand=brand,
            budget=budget,
            summarize=summarize,
            searx_client=searx_client,
            firecrawl_client=firecrawl_client,
        )
        pending = store.count_pending(brand=brand)
        if pending > 0:
            time.sleep(1.0)
            continue
        record_heartbeat(
            ACTOR,
            status=AgentStatus.IDLE,
            task="engagement queue empty; waiting for new queries",
        )
        time.sleep(WATCH_POLL_SECONDS)


def _load_venue(resource_id: int | None) -> HuntResource | None:
    if resource_id is None:
        return None
    from agent_crm.db import session_scope
    from agent_crm.models import HuntResource as HuntResourceModel

    with session_scope() as session:
        return session.get(HuntResourceModel, resource_id)


def _run_engagement_query(
    *,
    query: str,
    brand: Brand,
    venue: HuntResource | None,
    budget: EngagementBudget,
    summarize: bool,
    searx_client: httpx.Client | None,
    firecrawl_client: httpx.Client | None,
    queue: EngagementQueryStore,
) -> dict[str, int]:
    settings = get_settings()
    venue_class = None
    if venue is not None:
        venue_class = classify_resource_detailed(venue.url, venue.title, venue.notes)

    threads = 0
    drafts = 0
    pages = 0
    seen: set[str] = set()
    serp_dicts: list[dict[str, Any]] = []
    page_texts: list[str] = []

    try:
        hits = search(
            query,
            limit=min(settings.hunter_search_result_limit, 20),
            client=searx_client,
        )
    except SearxngError:
        return {"threads": 0, "drafts": 0, "pages": 0, "follow_ups": 0}

    for hit in hits:
        serp_dicts.append({"title": hit.title, "url": hit.url, "content": hit.snippet})
        if pages >= budget.max_pages_per_venue:
            break
        if hit.url in seen:
            continue
        if not is_public_http_url(hit.url, resolve_dns=False):
            continue
        seen.add(hit.url)

        markdown = hit.snippet or ""
        title = hit.title
        if is_thread_url(hit.url) or pages < budget.max_pages_per_venue:
            record_heartbeat(
                ACTOR,
                status=AgentStatus.WORKING,
                task=f"scraping {hit.url}",
                resource=settings.firecrawl_url,
            )
            try:
                page = scrape(hit.url, client=firecrawl_client)
                title = page.title or hit.title
                markdown = page.markdown or hit.snippet or ""
                pages += 1
                if page.markdown:
                    page_texts.append(page.markdown[:8000])
            except FirecrawlError:
                if not is_thread_url(hit.url):
                    continue

        if is_junk_title(title) and not is_thread_url(hit.url):
            continue

        hit_class = classify_resource_detailed(hit.url, title, markdown)
        if is_engagement_venue(hit_class, hit.url):
            upsert = HuntStore().upsert_resource(
                url=hit.url,
                brand=brand,
                title=title,
                found_via_query=query,
                snippet=(markdown or hit.snippet or "")[:500] or None,
                kind=hit_class.kind,
            )
            if upsert.is_new and upsert.resource is not None:
                for term in venue_scan_queries(hit_class, url=hit.url):
                    queue.enqueue_query(
                        query=term,
                        brand=brand,
                        origin=f"venue:{upsert.resource.domain}"[:128],
                        hunt_resource_id=upsert.resource.id,
                    )

        signals = extract_engagement_signals(
            title, hit.snippet, markdown, kind=hit_class.kind
        )
        if not is_thread_url(hit.url) and signals.score < settings.engagement_popularity_threshold:
            continue

        thread = upsert_thread(
            url=hit.url,
            brand=brand,
            title=title,
            signals=signals,
            hunt_resource_id=venue.id if venue is not None else None,
            platform=hit_class.platform or (venue_class.platform if venue_class else None),
            venue_url=(
                venue_url_from_thread(hit.url, hit_class)
                or (venue.url if venue is not None else None)
            ),
            excerpt=markdown[:800],
            found_via_query=query,
            scanned=True,
        )
        if thread is None:
            continue
        threads += 1
        mark_thread_scanned(
            thread.id, interval_hours=settings.engagement_scan_interval_hours
        )

        if (
            summarize
            and signals.score >= settings.engagement_draft_threshold
            and not has_draft(thread.id, brand)
        ):
            drafted = _maybe_draft_reply(thread, markdown=markdown, brand=brand)
            if drafted:
                drafts += 1

    follow_ups = _enqueue_engagement_follow_ups(
        queue,
        query=query,
        brand=brand,
        hunt_resource_id=venue.id if venue is not None else None,
        serp_results=serp_dicts,
        page_texts=page_texts,
        summarize=summarize,
    )
    return {
        "threads": threads,
        "drafts": drafts,
        "pages": pages,
        "follow_ups": follow_ups,
    }


def _enqueue_engagement_follow_ups(
    store: EngagementQueryStore,
    *,
    query: str,
    brand: Brand,
    hunt_resource_id: int | None,
    serp_results: list[dict[str, Any]],
    page_texts: list[str],
    summarize: bool,
) -> int:
    settings = get_settings()
    max_terms = settings.engagement_max_branch_terms
    heuristic = extract_engagement_follow_up_terms(
        query=query,
        brand=brand,
        serp_results=serp_results,
        page_texts=page_texts,
        max_terms=max_terms,
    )
    llm_terms: list[str] = []
    if summarize:
        llm_terms = _llm_engagement_follow_up_terms(
            query=query,
            brand=brand,
            serp_results=serp_results,
            page_texts=page_texts,
            max_terms=max_terms,
        )
    merged: list[str] = []
    seen: set[str] = {EngagementQueryStore.make_dedupe_key(brand, query)}
    for term in heuristic + llm_terms:
        key = EngagementQueryStore.make_dedupe_key(brand, term)
        if key in seen:
            continue
        seen.add(key)
        merged.append(term)
        if len(merged) >= max_terms:
            break
    origin = f"branch:{query}".strip()[:128]
    enqueued = 0
    for term in merged:
        if store.enqueue_query(
            query=term,
            brand=brand,
            origin=origin,
            hunt_resource_id=hunt_resource_id,
        ):
            enqueued += 1
    return enqueued


def _llm_engagement_follow_up_terms(
    *,
    query: str,
    brand: Brand,
    serp_results: list[dict[str, Any]],
    page_texts: list[str],
    max_terms: int,
) -> list[str]:
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
        f"You help a CRM engagement agent find more high-traffic forums, subreddits, "
        f"Discords, and weekly threads for {brand.value}.\n"
        f"Original query: {wrap_untrusted('query', query, max_chars=300)}\n"
        "Search hits and page excerpts:\n"
        + "\n".join(lines)
        + "\n\n"
        f"Suggest up to {max_terms} NEW search queries to find more communities or "
        "popular threads mentioned in the sources. Focus on venues, not people. "
        "Do NOT invent emails, person names, or URLs. Skip news headlines, "
        "product recalls, sports, and weather — they are off-topic.\n"
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
                "max_tokens": 240,
            },
            timeout=120.0,
            actor=ACTOR,
            task=f"engagement follow-ups for {query[:40]}",
        )
        content = _extract_chat_content(response) or ""
    except Exception:  # noqa: BLE001
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


def _maybe_draft_reply(
    thread: EngagementThread,
    *,
    markdown: str,
    brand: Brand,
) -> bool:
    """Draft a helpful product-related comment. Never posts."""
    settings = get_settings()
    brand_context = brand_context_for(ACTOR, brand, channel="engage")
    system = (
        "You draft a single public forum comment for a CRM agent. "
        "Discovery only — do not claim the comment was posted, do not invent proof, "
        "and do not include emails, URLs to login walls, or calls to buy immediately. "
        "If the thread is a poor fit, set should_skip true. "
        "Be helpful first; mention the product only when it naturally answers the post."
        + UNTRUSTED_DATA_SYSTEM_SUFFIX
    )
    if has_skill(ACTOR, "social-media") or has_skill(ACTOR, "social-media/post-package"):
        draft_guidance = engagement_draft_guidance()
        if draft_guidance:
            system += f"\n\n--- social-media engagement rules ---\n{draft_guidance}"
    if brand_context:
        system += f"\n\n--- brand context (excerpt) ---\n{brand_context}"
    user = (
        f"Brand: {brand.value}\n"
        f"{wrap_untrusted('thread_url', thread.url, max_chars=400)}\n"
        f"{wrap_untrusted('title', thread.title, max_chars=300)}\n"
        f"{wrap_untrusted('excerpt', markdown, max_chars=2500)}\n\n"
        'Return JSON: {"draft": "...", "product_angle": "...", '
        '"should_skip": false, "skip_reason": null}'
    )
    try:
        record_heartbeat(
            ACTOR,
            status=AgentStatus.WORKING,
            task=f"drafting reply for {thread.url}",
            resource=f"Spark queue ({settings.llm_base_url})",
        )
        response = chat_completions(
            {
                "model": "crm",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 280,
                "temperature": 0.3,
            },
            timeout=120.0,
            actor=ACTOR,
            task=f"draft {thread.url[:40]}",
        )
        content = _extract_chat_content(response)
        parsed = extract_json_object(content or "")
        if not parsed or parsed.get("should_skip") is True:
            return False
        draft = str(parsed.get("draft") or "").strip()
        if not draft:
            return False
        angle = parsed.get("product_angle")
        row = upsert_draft(
            thread_id=thread.id,
            brand=brand,
            draft_text=draft,
            product_angle=str(angle).strip() if isinstance(angle, str) else None,
        )
        if row is None:
            return False
        mark_thread_draft_ready(thread.id)
        return True
    except Exception:  # noqa: BLE001 — drafting must not block the scan
        return False


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
