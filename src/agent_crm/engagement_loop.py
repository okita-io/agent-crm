"""Agent engagement loop: rescan catalogued forums and draft comment replies.

Discovery only. This agent never posts, creates accounts, or sends mail.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import get_settings
from .engagement import (
    extract_engagement_signals,
    is_thread_url,
    venue_scan_queries,
    venue_url_from_thread,
)
from .engagement_store import (
    has_draft,
    list_venues_due_for_scan,
    mark_thread_draft_ready,
    mark_thread_scanned,
    mark_venue_scanned,
    upsert_draft,
    upsert_thread,
)
from .enums import ActivityType, AgentStatus, Brand, ImprovementSourceAgent
from .firecrawl_client import FirecrawlError, scrape
from .heartbeat import record_heartbeat
from .hunt_utils import classify_resource_detailed, is_junk_title
from .llm_client import chat_completions
from .llm_text import UNTRUSTED_DATA_SYSTEM_SUFFIX, extract_json_object, wrap_untrusted
from .marketing_skill import brand_context_snippet
from .models import EngagementThread, HuntResource
from .searxng_client import SearxngError, search
from .tooling import CRMToolkit
from .url_safety import is_public_http_url

ACTOR = "engagement"


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
    errors: list[str] = field(default_factory=list)
    stop_reason: str = "queue_empty"


def run_engagement_loop(
    *,
    brand: Brand | None = None,
    budget: EngagementBudget | None = None,
    summarize: bool = True,
    searx_client: httpx.Client | None = None,
    firecrawl_client: httpx.Client | None = None,
) -> EngagementLoopResult:
    """Scan catalogued high-engagement venues and draft replies (never post)."""
    settings = get_settings()
    budget = budget or EngagementBudget(
        max_venues=settings.engagement_max_venues_per_run,
        max_pages_per_venue=settings.engagement_max_pages_per_venue,
        max_minutes=settings.engagement_max_minutes_default,
    )
    crm = CRMToolkit(actor=ACTOR)
    result = EngagementLoopResult()
    deadline = None if budget.max_minutes <= 0 else time.monotonic() + budget.max_minutes * 60

    venues = list_venues_due_for_scan(brand=brand, limit=budget.max_venues)
    if not venues:
        record_heartbeat(ACTOR, status=AgentStatus.IDLE, task="no venues due")
        result.stop_reason = "queue_empty"
        return result

    crm.log_note(
        f"Engagement scan started ({len(venues)} venues)",
        type=ActivityType.NOTE,
        payload={"venues": len(venues), "brand": brand.value if brand else None},
    )

    for venue in venues:
        if deadline is not None and time.monotonic() >= deadline:
            result.stop_reason = "max_minutes"
            break
        if result.venues_scanned >= budget.max_venues:
            result.stop_reason = "max_venues"
            break

        record_heartbeat(
            ACTOR,
            status=AgentStatus.THINKING,
            task=f"scanning {venue.domain}",
            resource=settings.searxng_url,
        )
        try:
            stats = _scan_venue(
                venue,
                budget=budget,
                summarize=summarize,
                searx_client=searx_client,
                firecrawl_client=firecrawl_client,
            )
        except Exception as exc:  # noqa: BLE001
            from .orchestrator import note_worker_failure

            message = f"engagement scan failed for {venue.url}: {exc}"
            result.errors.append(message)
            note_worker_failure(
                source_agent=ImprovementSourceAgent.ENGAGEMENT_LOOP,
                error_text=str(exc),
                context=f"venue {venue.id}",
            )
            continue

        result.venues_scanned += 1
        result.threads_cataloged += stats["threads"]
        result.drafts_written += stats["drafts"]
        result.pages_scraped += stats["pages"]
        mark_venue_scanned(venue.id, interval_hours=settings.engagement_scan_interval_hours)

    record_heartbeat(
        ACTOR,
        status=AgentStatus.IDLE,
        task=(
            f"loop stopped ({result.stop_reason}): {result.venues_scanned} venues, "
            f"{result.threads_cataloged} threads, {result.drafts_written} drafts"
        ),
    )
    if result.stop_reason == "queue_empty" and result.venues_scanned:
        result.stop_reason = "complete"
    return result


def _scan_venue(
    venue: HuntResource,
    *,
    budget: EngagementBudget,
    summarize: bool,
    searx_client: httpx.Client | None,
    firecrawl_client: httpx.Client | None,
) -> dict[str, int]:
    settings = get_settings()
    classification = classify_resource_detailed(venue.url, venue.title, venue.notes)
    queries = venue_scan_queries(classification, url=venue.url)
    if not queries:
        queries = [f"site:{venue.domain} popular threads"]

    threads = 0
    drafts = 0
    pages = 0
    seen: set[str] = set()

    for query in queries:
        if pages >= budget.max_pages_per_venue:
            break
        try:
            hits = search(
                query,
                limit=min(settings.hunter_search_result_limit, 20),
                client=searx_client,
            )
        except SearxngError:
            continue

        for hit in hits:
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
                except FirecrawlError:
                    if not is_thread_url(hit.url):
                        continue

            if is_junk_title(title) and not is_thread_url(hit.url):
                continue

            hit_class = classify_resource_detailed(hit.url, title, markdown)
            signals = extract_engagement_signals(
                title, hit.snippet, markdown, kind=hit_class.kind
            )
            if not is_thread_url(hit.url) and signals.score < settings.engagement_popularity_threshold:
                continue

            thread = upsert_thread(
                url=hit.url,
                brand=venue.brand,
                title=title,
                signals=signals,
                hunt_resource_id=venue.id,
                platform=hit_class.platform or classification.platform,
                venue_url=venue_url_from_thread(hit.url, hit_class) or venue.url,
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
                and not has_draft(thread.id, venue.brand)
            ):
                drafted = _maybe_draft_reply(thread, markdown=markdown, brand=venue.brand)
                if drafted:
                    drafts += 1

    return {"threads": threads, "drafts": drafts, "pages": pages}


def _maybe_draft_reply(
    thread: EngagementThread,
    *,
    markdown: str,
    brand: Brand,
) -> bool:
    """Draft a helpful product-related comment. Never posts."""
    settings = get_settings()
    brand_context = brand_context_snippet(brand)
    system = (
        "You draft a single public forum comment for a CRM agent. "
        "Discovery only — do not claim the comment was posted, do not invent proof, "
        "and do not include emails, URLs to login walls, or calls to buy immediately. "
        "If the thread is a poor fit, set should_skip true. "
        "Be helpful first; mention the product only when it naturally answers the post."
        + UNTRUSTED_DATA_SYSTEM_SUFFIX
    )
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
