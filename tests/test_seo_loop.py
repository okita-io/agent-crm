"""Tests for the SEO document loop (mocked Firecrawl/SearXNG, no live deploys)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine, session_scope
from agent_crm.enums import Brand, SeoPlanKind, SeoReviewKind, SeoTargetRole
from agent_crm.models import SeoTarget
from agent_crm.seo_loop import SeoBudget, run_seo_loop
from agent_crm.seo_query_store import SeoQueryStore
from agent_crm.seo_store import list_plans, list_reviews, list_targets


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

    with session_scope() as session:
        rows = list(session.scalars(select(SeoTarget)))
        for row in rows:
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
