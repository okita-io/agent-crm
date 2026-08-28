"""Tests for the ad-placement research loop."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import Brand, ResearchFindingKind
from agent_crm.research_loop import ResearchLoopBudget, run_research_loop
from agent_crm.schemas import ResearchResult


def _setup_db(tmp_path, monkeypatch, name: str) -> None:
    db_path = tmp_path / name
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()


def _teardown_db() -> None:
    reset_engine()
    get_settings.cache_clear()


def test_research_loop_cycles_brands_on_ad_placement(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch, "research-loop.db")
    seen: list[tuple[Brand, str | None]] = []

    def fake_run_research(request):
        seen.append((request.brand, request.query))
        return ResearchResult(
            brand=request.brand,
            kind=ResearchFindingKind.AD_PLACEMENT,
            queries_run=1,
            pages_scraped=1,
            findings_written=[len(seen)],
            errors=[],
        )

    with patch("agent_crm.research_loop.run_research", side_effect=fake_run_research):
        result = run_research_loop(
            budget=ResearchLoopBudget(max_queries=4, max_pages=4, max_minutes=5),
            summarize=False,
            write_accounts=False,
        )

    assert result.queries_run == 4
    assert result.pages_scraped == 4
    assert len(seen) == 4
    brands = [brand for brand, _ in seen]
    assert brands == [
        Brand.CELESTIAL_NEXUS,
        Brand.MIDNIGHTSATIN,
        Brand.HEYBUDDY,
        Brand.TACTIC_STUDIO,
    ]
    assert all(query for _, query in seen)
    _teardown_db()


def test_research_loop_keeps_queue_after_drain(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch, "research-loop-grow.db")
    from agent_crm.research_query_store import ResearchQueryStore
    from agent_crm.research_seeds import loop_seed_entries

    def fake_run_research(request):
        from agent_crm.research_query_store import ResearchQueryStore as Store

        Store().enqueue_query(
            query=f"follow-up from {request.query}",
            brand=request.brand,
            kind=request.kind,
            origin="branch:test",
        )
        return ResearchResult(
            brand=request.brand,
            kind=request.kind,
            queries_run=1,
            pages_scraped=1,
            findings_written=[1],
            errors=[],
            follow_up_terms_enqueued=1,
        )

    seed_count = len(loop_seed_entries())
    with patch("agent_crm.research_loop.run_research", side_effect=fake_run_research):
        result = run_research_loop(
            budget=ResearchLoopBudget(max_queries=4, max_pages=4, max_minutes=5),
            summarize=False,
            write_accounts=False,
        )

    store = ResearchQueryStore()
    assert result.follow_up_terms_enqueued == 4
    assert store.count_all() >= seed_count + 4
    assert store.count_pending() >= 1
    _teardown_db()


def test_research_loop_stops_on_query_budget(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch, "research-loop-budget.db")

    with patch("agent_crm.research_loop.run_research") as mock_run:
        mock_run.return_value = ResearchResult(
            brand=Brand.CELESTIAL_NEXUS,
            kind=ResearchFindingKind.AD_PLACEMENT,
            queries_run=1,
            pages_scraped=1,
            findings_written=[1],
            errors=[],
        )
        result = run_research_loop(
            budget=ResearchLoopBudget(max_queries=2, max_pages=10, max_minutes=5),
            summarize=False,
            write_accounts=False,
        )

    assert result.queries_run == 2
    assert result.stop_reason == "query_budget"
    assert mock_run.call_count == 2
    _teardown_db()
