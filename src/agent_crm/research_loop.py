"""Bounded ad-placement research loop across all four brands."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import get_settings
from .enums import Brand, ResearchFindingKind
from .research import run_research
from .research_seeds import seed_queries
from .schemas import ResearchRequest

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


def run_research_loop(
    *,
    budget: ResearchLoopBudget | None = None,
    summarize: bool = True,
    write_accounts: bool = True,
) -> ResearchLoopResult:
    """Cycle the four brands on ad-placement seed queries until budgets or seeds exhaust."""
    budget = budget or ResearchLoopBudget()
    kind = ResearchFindingKind.AD_PLACEMENT
    queries_by_brand = {brand: seed_queries(brand, kind) for brand in RESEARCH_LOOP_BRANDS}
    query_indices = {brand: 0 for brand in RESEARCH_LOOP_BRANDS}

    result = ResearchLoopResult()
    started = time.monotonic()
    brand_cycle = 0
    idle_rounds = 0

    while True:
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
        idx = query_indices[brand]
        brand_queries = queries_by_brand[brand]
        if idx >= len(brand_queries):
            idle_rounds += 1
            if idle_rounds >= len(RESEARCH_LOOP_BRANDS):
                result.stop_reason = "queue_empty"
                break
            continue

        idle_rounds = 0
        query = brand_queries[idx]
        query_indices[brand] = idx + 1

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
        result.errors.extend(run_result.errors)
        for error in run_result.errors:
            from .enums import ImprovementSourceAgent
            from .orchestrator import note_worker_failure

            note_worker_failure(
                source_agent=ImprovementSourceAgent.RESEARCH_LOOP,
                error_text=error,
                context=f"research {brand.value} query",
            )

        if run_result.queries_run == 0:
            result.stop_reason = "query_failed"
            break

    return result
