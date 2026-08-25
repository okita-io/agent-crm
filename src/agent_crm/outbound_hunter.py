"""Outbound Hunter: one-shot search and bounded branching loop."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agent_crm.config import Settings, get_settings
from agent_crm.enums import AgentHeartbeatStatus, Brand, HuntQueryStatus, LeadSource, Stage
from agent_crm.firecrawl_client import FirecrawlClient
from agent_crm.hunt_seeds import seeds_for_brand
from agent_crm.hunt_store import HuntStore
from agent_crm.hunt_utils import extract_heuristic_terms, is_junk_title
from agent_crm.llm_client import LlmClient
from agent_crm.pipeline import PipelineManager
from agent_crm.schemas import LeadCreate
from agent_crm.searxng_client import SearchResult, SearxngClient
from agent_crm.tooling import CRMToolkit

# Rotate through these param sets so each term gets varied SearXNG coverage.
PARAM_PALETTES: list[dict | None] = [
    None,
    {"categories": "general", "pageno": 1},
    {"categories": "social media"},
    {"categories": "news", "time_range": "year"},
    {"categories": "it"},
]


@dataclass
class HuntBudget:
    max_queries: int = 20
    max_minutes: int = 25
    max_pages_per_query: int = 8

    def __post_init__(self) -> None:
        settings = get_settings()
        self.max_pages_per_query = min(self.max_pages_per_query, settings.hunter_max_pages_per_run)


@dataclass
class HuntRunResult:
    queries_run: int = 0
    resources_found: int = 0
    leads_created: int = 0
    branch_terms_enqueued: int = 0
    stop_reason: str = "queue_empty"
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])


class OutboundHunter:
    """Discover communities/directories via SearXNG, scrape via Firecrawl, collect resources."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        searxng: SearxngClient | None = None,
        firecrawl: FirecrawlClient | None = None,
        llm: LlmClient | None = None,
        store: HuntStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.searxng = searxng or SearxngClient(self.settings)
        self.firecrawl = firecrawl or FirecrawlClient(self.settings)
        self.llm = llm or LlmClient(self.settings)
        self.store = store or HuntStore()
        self.crm = CRMToolkit(actor="outbound_hunter")
        self.pipeline = PipelineManager(actor="outbound_hunter")

    def hunt_once(
        self,
        query: str,
        *,
        brand: Brand = Brand.UNASSIGNED,
        max_pages: int | None = None,
        params: dict | None = None,
    ) -> dict:
        """One-shot hunt: search, collect resources, optionally create leads."""
        max_pages = max_pages or self.settings.hunter_max_pages_per_run
        self.store.set_heartbeat(AgentHeartbeatStatus.THINKING, f"Searching: {query}")

        search_params = params or {}
        results = self.searxng.search(query, **search_params)
        resources = self._collect_from_results(query, brand, results)

        pages_scraped = 0
        if results and max_pages > 0:
            self.store.set_heartbeat(AgentHeartbeatStatus.WORKING, f"Scraping up to {max_pages} pages")
            pages_scraped = self._scrape_top_results(
                query, brand, results[:max_pages]
            )
            resources += pages_scraped

        leads_created = self._maybe_create_leads(query, brand, results)

        self.store.set_heartbeat(AgentHeartbeatStatus.IDLE, "One-shot hunt complete")
        return {
            "query": query,
            "brand": brand.value,
            "results_count": len(results),
            "resources_collected": resources,
            "pages_scraped": pages_scraped,
            "leads_created": leads_created,
            "params": search_params,
        }

    def hunt_loop(
        self,
        *,
        query: str | None = None,
        brand: Brand = Brand.UNASSIGNED,
        budget: HuntBudget | None = None,
        resume: bool = True,
    ) -> HuntRunResult:
        """Bounded branching loop: queue terms, vary params, collect resources."""
        budget = budget or HuntBudget(
            max_queries=self.settings.hunter_max_queries_default,
            max_minutes=self.settings.hunter_max_minutes_default,
            max_pages_per_query=self.settings.hunter_max_pages_per_run,
        )
        result = HuntRunResult()
        started = time.monotonic()
        deadline = started + budget.max_minutes * 60

        pending_existing = self.store.count_pending(brand if brand != Brand.UNASSIGNED else None)
        if resume and not query and pending_existing > 0:
            should_seed = False
        else:
            should_seed = bool(query) or brand != Brand.UNASSIGNED

        if should_seed:
            if query:
                self.store.enqueue_query(
                    query=query, brand=brand, origin="seed", run_id=result.run_id
                )
            elif brand != Brand.UNASSIGNED:
                for seed in seeds_for_brand(brand):
                    self.store.enqueue_query(
                        query=seed, brand=brand, origin="seed_pack", run_id=result.run_id
                    )
            else:
                result.stop_reason = "no_seed"
                self.store.set_heartbeat(AgentHeartbeatStatus.IDLE, "No seed query or brand pack")
                return result

        use_run_id = None if resume else result.run_id
        brand_filter = brand if brand != Brand.UNASSIGNED else None

        palette_index = 0
        while result.queries_run < budget.max_queries and time.monotonic() < deadline:
            pending = self.store.next_pending_query(run_id=use_run_id, brand=brand_filter)
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

            self.store.mark_query_running(pending.id)
            self.store.set_heartbeat(
                AgentHeartbeatStatus.THINKING,
                f"Query {result.queries_run + 1}/{budget.max_queries}: {pending.query}",
            )

            try:
                run_stats = self._run_queued_query(
                    pending.query,
                    brand=pending.brand,
                    params=params,
                    max_pages=budget.max_pages_per_query,
                    origin=pending.origin,
                    run_id=pending.run_id or result.run_id,
                )
            except Exception as exc:  # noqa: BLE001 — hunter should survive single-query failures
                self.store.mark_query_failed(pending.id, str(exc))
                continue

            result.queries_run += 1
            result.resources_found += run_stats["resources_collected"]
            result.leads_created += run_stats["leads_created"]
            result.branch_terms_enqueued += run_stats["branch_terms_enqueued"]
            self.store.mark_query_completed(pending.id)

            if time.monotonic() >= deadline:
                result.stop_reason = "max_minutes"
                break
            if result.queries_run >= budget.max_queries:
                result.stop_reason = "max_queries"
                break

        if result.stop_reason == "queue_empty" and result.queries_run >= budget.max_queries:
            result.stop_reason = "max_queries"

        self.store.set_heartbeat(
            AgentHeartbeatStatus.IDLE,
            f"Loop stopped ({result.stop_reason}): {result.queries_run} queries, "
            f"{result.resources_found} resources",
        )
        return result

    def _run_queued_query(
        self,
        query: str,
        *,
        brand: Brand,
        params: dict,
        max_pages: int,
        origin: str,
        run_id: str | None = None,
    ) -> dict:
        results = self.searxng.search(query, **params)
        result_dicts = [
            {"title": r.title, "url": r.url, "content": r.content} for r in results
        ]

        resources = self._collect_from_results(query, brand, results)

        self.store.set_heartbeat(AgentHeartbeatStatus.WORKING, f"Scraping for: {query}")
        pages_scraped = 0
        if results and max_pages > 0:
            pages_scraped = self._scrape_top_results(query, brand, results[:max_pages])
            resources += pages_scraped

        leads_created = self._maybe_create_leads(query, brand, results)

        branch_terms = self._extract_branch_terms(query, result_dicts)
        enqueued = 0
        for term in branch_terms:
            if self.store.enqueue_query(
                query=term,
                brand=brand,
                origin=f"branch:{origin}",
                params=None,
                run_id=run_id,
            ):
                enqueued += 1

        return {
            "resources_collected": resources,
            "pages_scraped": pages_scraped,
            "leads_created": leads_created,
            "branch_terms_enqueued": enqueued,
        }

    def _collect_from_results(
        self,
        query: str,
        brand: Brand,
        results: list[SearchResult],
    ) -> int:
        count = 0
        for result in results:
            row = self.store.upsert_resource(
                url=result.url,
                brand=brand,
                title=result.title,
                found_via_query=query,
                snippet=(result.content or "")[:500] or None,
            )
            if row is not None:
                count += 1
        return count

    def _scrape_top_results(
        self,
        query: str,
        brand: Brand,
        results: list[SearchResult],
    ) -> int:
        count = 0
        for result in results:
            scraped = self.firecrawl.scrape(result.url)
            if scraped is None:
                continue
            title = scraped.title or result.title
            if is_junk_title(title):
                continue
            row = self.store.upsert_resource(
                url=result.url,
                brand=brand,
                title=title,
                found_via_query=query,
                snippet=(scraped.markdown or "")[:500] or None,
            )
            if row is not None:
                count += 1
        return count

    def _maybe_create_leads(
        self,
        query: str,
        brand: Brand,
        results: list[SearchResult],
    ) -> int:
        """Create leads only for results that look like real names (best-effort)."""
        created = 0
        for result in results[:5]:
            title = (result.title or "").strip()
            if is_junk_title(title) or "@" in title:
                continue
            if len(title.split()) > 6:
                continue
            lead = self.crm.create_lead(
                LeadCreate(
                    name=title[:255],
                    company=None,
                    source=LeadSource.HUNTER,
                    raw_payload={
                        "query": query,
                        "url": result.url,
                        "snippet": result.content,
                        "hunted_at": datetime.now(UTC).isoformat(),
                    },
                )
            )
            if brand != Brand.UNASSIGNED:
                self.crm.route_brand(lead.id, brand)
            self.pipeline.transition(lead.id, Stage.PROSPECT)
            created += 1
        return created

    def _extract_branch_terms(self, query: str, results: list[dict]) -> list[str]:
        max_terms = self.settings.hunter_max_branch_terms
        heuristic = extract_heuristic_terms(results, max_terms=max_terms)
        if self.llm.enabled:
            llm_terms = self.llm.extract_follow_up_terms(
                query=query, results=results, max_terms=max_terms
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
        return heuristic
