"""Tests for the SEO document loop (mocked Firecrawl/SearXNG, no live deploys)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine, session_scope
from agent_crm.enums import Brand, SeoPlanKind, SeoReviewKind, SeoTargetRole
from agent_crm.models import SeoTarget
from agent_crm.seo_loop import SeoBudget, SeoLoopResult, run_seo_loop, run_seo_loop_watch
from agent_crm.seo_query_store import SeoQueryStore
from agent_crm.seo_store import list_plans, list_reviews, list_targets, next_noon_at


@pytest.fixture()
def seo_db(tmp_path, monkeypatch):
    db_path = tmp_path / "seo-loop.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield db_path
    reset_engine()
    get_settings.cache_clear()


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/search":
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://www.galatea.com/romance",
                        "title": "Galatea serialized romance stories",
                        "content": "Read romance serials on mobile",
                    }
                ]
            },
        )
    if request.url.path == "/v1/scrape":
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}
        url = str(payload.get("url") or "")
        if "galatea" in url or "dreame" in url or "radish" in url:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "markdown": (
                            "# Serialized romance app\n\n"
                            "Read chapters every week on your phone. " * 15
                        ),
                        "metadata": {
                            "title": "Serialized romance | Galatea",
                            "description": "Romance serials for mobile readers who want daily chapters.",
                            "canonical": url,
                            "ogTitle": "Serialized romance | Galatea",
                        },
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "markdown": (
                        "MidnightSatin romance serials.\n\n"
                        "[About](https://midnightsatin.app/about)\n"
                        "![](https://midnightsatin.app/cover.png)\n"
                    ),
                    "metadata": {"statusCode": 200},
                }
            },
        )
    return httpx.Response(404)


def test_seo_loop_writes_owned_review_and_plan(seo_db) -> None:
    http = httpx.Client(transport=httpx.MockTransport(_handler))
    with patch("agent_crm.seo_loop.chat_completions") as mock_llm:
        result = run_seo_loop(
            brand=Brand.MIDNIGHTSATIN,
            budget=SeoBudget(max_targets=1, max_pages_per_target=2, max_minutes=5),
            summarize=False,
            searx_client=http,
            firecrawl_client=http,
        )
        mock_llm.assert_not_called()

    assert result.reviews_written == 1
    assert result.plans_written == 1
    assert result.pages_scraped >= 1
    assert result.stop_reason == "max_targets"

    targets = list_targets(brand=Brand.MIDNIGHTSATIN)
    assert any(row.role == SeoTargetRole.OWNED for row in targets)
    reviews = list_reviews(brand=Brand.MIDNIGHTSATIN)
    assert len(reviews) == 1
    assert reviews[0].kind == SeoReviewKind.SITE_AUDIT
    assert reviews[0].status.value == "draft"
    assert "Do not implement" not in reviews[0].body or "one thing" in reviews[0].body.lower()
    assert reviews[0].one_thing
    assert reviews[0].issues
    plans = list_plans(brand=Brand.MIDNIGHTSATIN)
    assert len(plans) == 1
    assert plans[0].kind == SeoPlanKind.MIXED
    assert "will not change the live site" in plans[0].body.lower()
    assert plans[0].review_id == reviews[0].id


def test_seo_loop_competitor_review_has_no_plan(seo_db) -> None:
    http = httpx.Client(transport=httpx.MockTransport(_handler))
    result = run_seo_loop(
        brand=Brand.MIDNIGHTSATIN,
        budget=SeoBudget(max_targets=4, max_pages_per_target=1, max_minutes=5),
        summarize=False,
        searx_client=http,
        firecrawl_client=http,
    )
    assert result.reviews_written >= 2
    reviews = list_reviews(brand=Brand.MIDNIGHTSATIN)
    kinds = {row.kind for row in reviews}
    assert SeoReviewKind.SITE_AUDIT in kinds
    assert SeoReviewKind.COMPETITOR in kinds
    plans = list_plans(brand=Brand.MIDNIGHTSATIN)
    assert len(plans) == 1
    assert all(row.kind == SeoPlanKind.MIXED for row in plans)


def test_seo_queue_reopens_completed_when_due(seo_db) -> None:
    http = httpx.Client(transport=httpx.MockTransport(_handler))
    first = run_seo_loop(
        brand=Brand.MIDNIGHTSATIN,
        budget=SeoBudget(max_targets=1, max_pages_per_target=1, max_minutes=5),
        summarize=False,
        searx_client=http,
        firecrawl_client=http,
    )
    assert first.reviews_written == 1
    store = SeoQueryStore()
    completed_before = store.queue_status()["completed"]
    assert completed_before >= 1

    yesterday = datetime.now(UTC) - timedelta(days=1)
    with session_scope() as session:
        rows = list(session.scalars(select(SeoTarget)))
        for row in rows:
            row.last_reviewed_at = yesterday
            row.next_review_at = datetime.now(UTC) - timedelta(hours=1)

    second = run_seo_loop(
        brand=Brand.MIDNIGHTSATIN,
        budget=SeoBudget(max_targets=1, max_pages_per_target=1, max_minutes=5),
        summarize=False,
        searx_client=http,
        firecrawl_client=http,
    )
    assert second.reviews_written == 1
    assert store.count_all(brand=Brand.MIDNIGHTSATIN) >= completed_before


def test_next_noon_before_local_noon_is_today() -> None:
    la = ZoneInfo("America/Los_Angeles")
    nxt = next_noon_at(datetime(2026, 8, 28, 9, 0, tzinfo=la))
    assert nxt.astimezone(la) == datetime(2026, 8, 28, 12, 0, tzinfo=la)


def test_next_noon_after_local_noon_is_tomorrow() -> None:
    la = ZoneInfo("America/Los_Angeles")
    nxt = next_noon_at(datetime(2026, 8, 28, 13, 24, tzinfo=la))
    assert nxt.astimezone(la) == datetime(2026, 8, 29, 12, 0, tzinfo=la)


def test_seo_loop_schedules_next_pass_at_local_noon(seo_db) -> None:
    http = httpx.Client(transport=httpx.MockTransport(_handler))
    run_seo_loop(
        brand=Brand.MIDNIGHTSATIN,
        budget=SeoBudget(max_targets=1, max_pages_per_target=1, max_minutes=5),
        summarize=False,
        searx_client=http,
        firecrawl_client=http,
    )
    la = ZoneInfo("America/Los_Angeles")
    with session_scope() as session:
        reviewed = [
            row
            for row in session.scalars(select(SeoTarget))
            if row.last_reviewed_at is not None
        ]
    assert reviewed
    nxt = reviewed[0].next_review_at
    assert nxt is not None
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=UTC)
    local = nxt.astimezone(la)
    assert local.hour == 12
    assert local.minute == 0
    assert local.date() >= datetime.now(la).date()


def test_watch_idles_until_next_noon(seo_db, monkeypatch) -> None:
    from agent_crm import seo_loop as seo_loop_mod

    runs = {"n": 0}

    def fake_run(**kwargs):
        runs["n"] += 1
        return SeoLoopResult(stop_reason="queue_empty")

    def boom(seconds):
        raise KeyboardInterrupt(str(seconds))

    monkeypatch.setattr(seo_loop_mod, "run_seo_loop", fake_run)
    monkeypatch.setattr(seo_loop_mod, "WATCH_POLL_SECONDS", 0.01)
    monkeypatch.setattr(seo_loop_mod.time, "sleep", boom)
    with pytest.raises(KeyboardInterrupt):
        run_seo_loop_watch(summarize=False)
    assert runs["n"] == 1


def test_seo_budget_zero_targets_is_unlimited() -> None:
    budget = SeoBudget(max_targets=0, max_pages_per_target=4, max_minutes=0)
    assert budget.max_targets == 0
