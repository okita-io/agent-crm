"""Standing research loop: drain an append-only query queue across all four brands."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from agent_crm.agent_control import stop_if_disabled, wait_while_disabled
from agent_crm.config import get_settings
from agent_crm.enums import AgentStatus, Brand
from agent_crm.heartbeat import record_heartbeat
from .runner import run_research
from .query_store import ResearchQueryStore
from .seeds import loop_seed_entries
from agent_crm.schemas import ResearchRequest

ACTOR = "research"
WATCH_POLL_SECONDS = 60.0

RESEARCH_LOOP_BRANDS: tuple[Brand, ...] = (
    Brand.CELESTIAL_NEXUS,
    Brand.MIDNIGHTSATIN,
    Brand.HEYBUDDY,
    Brand.TACTIC_STUDIO,
)


@dataclass
class ResearchLoopBudget:
    max_queries: int = 0
    max_pages: int = 0
    max_minutes: int = 0
    search_limit: int = 50

    def __post_init__(self) -> None:
        settings = get_settings()
        self.search_limit = min(self.search_limit, settings.research_search_result_limit)


@dataclass
class ResearchLoopResult:
    queries_run: int = 0
    pages_scraped: int = 0
    findings_written: list[int] = field(default_factory=list)
    follow_up_terms_enqueued: int = 0
    errors: list[str] = field(default_factory=list)
    stop_reason: str = "queue_empty"


def _wall_clock_exhausted(started: float, max_minutes: int) -> bool:
    if max_minutes <= 0:
        return False
    return (time.monotonic() - started) / 60.0 >= max_minutes


def _remaining_minutes(started: float, max_minutes: int) -> int:
    settings = get_settings()
    if max_minutes <= 0:
        return settings.research_max_minutes_default
    elapsed = (time.monotonic() - started) / 60.0
    remaining = max_minutes - int(elapsed)
    return max(remaining, 1)


def _remaining_pages(max_pages: int, pages_scraped: int) -> int:
    settings = get_settings()
    if max_pages <= 0:
        return settings.research_max_pages_per_run
    return max(max_pages - pages_scraped, 1)


def _seed_research_queue(store: ResearchQueryStore) -> None:
    for brand, kind, query in loop_seed_entries():
        store.enqueue_query(
            query=query,
            brand=brand,
            kind=kind,
            origin="seed_pack",
        )


def run_research_loop(
    *,
    budget: ResearchLoopBudget | None = None,
    summarize: bool = True,
    write_accounts: bool = True,
) -> ResearchLoopResult:
    """Cycle brands on the persistent research queue until budgets exhaust.

    Seeds are inserted if missing. Follow-up terms from scraped pages are
    appended by ``run_research``. Rows are never deleted, so the queue only grows.
    """
    budget = budget or ResearchLoopBudget()
    if stop_if_disabled(ACTOR):
        result = ResearchLoopResult()
        result.stop_reason = "disabled"
        return result
    store = ResearchQueryStore()
    store.reset_stale_running_queries(stale_minutes=0)
    _seed_research_queue(store)

    result = ResearchLoopResult()
    started = time.monotonic()
    brand_cycle = 0
    idle_rounds = 0

    while True:
        if stop_if_disabled(ACTOR):
            result.stop_reason = "disabled"
            break
        if budget.max_queries > 0 and result.queries_run >= budget.max_queries:
            result.stop_reason = "query_budget"
            break
        if budget.max_pages > 0 and result.pages_scraped >= budget.max_pages:
            result.stop_reason = "page_budget"
            break
        if _wall_clock_exhausted(started, budget.max_minutes):
            result.stop_reason = "time_budget"
            break

        brand = RESEARCH_LOOP_BRANDS[brand_cycle % len(RESEARCH_LOOP_BRANDS)]
        brand_cycle += 1
        claimed = store.claim_next_pending_query(brand=brand)
        if claimed is None:
            idle_rounds += 1
            if idle_rounds >= len(RESEARCH_LOOP_BRANDS):
                result.stop_reason = "queue_empty"
                break
            continue

        idle_rounds = 0
        query = claimed.query
        kind = claimed.kind
        query_id = claimed.id

        run_result = run_research(
            ResearchRequest(
                brand=brand,
                kind=kind,
                query=query,
                max_queries=1,
                max_pages=_remaining_pages(budget.max_pages, result.pages_scraped),
                max_minutes=_remaining_minutes(started, budget.max_minutes),
                search_limit=budget.search_limit,
                summarize=summarize,
                write_accounts=write_accounts,
            )
        )

        result.queries_run += run_result.queries_run
        result.pages_scraped += run_result.pages_scraped
        result.findings_written.extend(run_result.findings_written)
        result.follow_up_terms_enqueued += run_result.follow_up_terms_enqueued
        result.errors.extend(run_result.errors)
        for error in run_result.errors:
            from agent_crm.enums import ImprovementSourceAgent
            from agent_crm.agency.orchestrator import note_worker_failure

            note_worker_failure(
                source_agent=ImprovementSourceAgent.RESEARCH_LOOP,
                error_text=error,
                context=f"research {brand.value} query",
            )

        if run_result.queries_run == 0:
            store.mark_query_failed(query_id, "; ".join(run_result.errors) or "query_failed")
            result.stop_reason = "query_failed"
            break

        store.mark_query_completed(query_id)

    return result


def run_research_loop_watch(
    *,
    budget: ResearchLoopBudget | None = None,
    summarize: bool = True,
    write_accounts: bool = True,
) -> None:
    """Drain the research queue forever, sleeping when the backlog is empty."""
    store = ResearchQueryStore()
    while True:
        wait_while_disabled(ACTOR)
        result = run_research_loop(
            budget=budget,
            summarize=summarize,
            write_accounts=write_accounts,
        )
        pending = store.count_pending()
        if pending > 0:
            time.sleep(1.0)
            continue
        record_heartbeat(
            ACTOR,
            status=AgentStatus.IDLE,
            task=(
                "research queue empty; waiting for new queries"
                if result.stop_reason == "queue_empty"
                else f"research idle after {result.stop_reason}"
            ),
        )
        time.sleep(WATCH_POLL_SECONDS)
