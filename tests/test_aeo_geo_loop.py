"""Tests for the AEO/GEO document loop (mocked Firecrawl, no live deploys)."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from agent_crm.aeo_geo.loop import AeoGeoLoopResult, run_aeo_geo_loop
from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import Brand, SeoPlanKind, SeoReviewKind, SeoTargetRole
from agent_crm.seo.loop import SeoBudget
from agent_crm.seo.store import list_plans, list_reviews, list_targets


@pytest.fixture()
def aeo_geo_db(tmp_path, monkeypatch):
    db_path = tmp_path / "aeo-geo-loop.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield db_path
    reset_engine()
    get_settings.cache_clear()


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/scrape":
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}
        url = str(payload.get("url") or "")
        if "galatea" in url:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "markdown": (
                            "# Romance serial app\n\n"
                            "Read chapters on your phone every week. " * 12
                        ),
                        "metadata": {
                            "title": "Galatea romance serials",
                            "description": "Serialized romance for mobile readers.",
                        },
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "markdown": (
                        "# MidnightSatin romance serials\n\n"
                        "Read dark romance on your phone.\n\n"
                        "[About](https://midnightsatin.app/about)\n"
                        "[Pricing](https://midnightsatin.app/pricing)\n"
                    ),
                    "metadata": {"title": "MidnightSatin", "statusCode": 200},
                }
            },
        )
    return httpx.Response(404)


def test_aeo_geo_loop_writes_owned_geo_review_and_plan(aeo_geo_db) -> None:
    http = httpx.Client(transport=httpx.MockTransport(_handler))
    with patch("agent_crm.aeo_geo.loop.chat_completions") as mock_llm:
        result = run_aeo_geo_loop(
            brand=Brand.MIDNIGHTSATIN,
            budget=SeoBudget(max_targets=1, max_pages_per_target=2, max_minutes=5),
            summarize=False,
            firecrawl_client=http,
        )
        mock_llm.assert_not_called()

    assert isinstance(result, AeoGeoLoopResult)
    assert result.reviews_written == 1
    assert result.plans_written == 1
    assert result.pages_scraped >= 1

    reviews = list_reviews(brand=Brand.MIDNIGHTSATIN, kind=SeoReviewKind.GEO)
    assert len(reviews) == 1
    assert reviews[0].kind == SeoReviewKind.GEO
    assert "AEO/GEO" in reviews[0].title
    assert "aeo" in reviews[0].body.lower()
    assert "geo" in reviews[0].body.lower()
    assert "[NEED:" in reviews[0].body
    assert reviews[0].one_thing

    plans = list_plans(brand=Brand.MIDNIGHTSATIN, kind=SeoPlanKind.GEO)
    assert len(plans) == 1
    assert plans[0].kind == SeoPlanKind.GEO
    assert "will not change the live site" in plans[0].body.lower()
    assert plans[0].review_id == reviews[0].id


def test_aeo_geo_loop_competitor_has_review_no_plan(aeo_geo_db) -> None:
    http = httpx.Client(transport=httpx.MockTransport(_handler))
    result = run_aeo_geo_loop(
        brand=Brand.MIDNIGHTSATIN,
        budget=SeoBudget(max_targets=4, max_pages_per_target=1, max_minutes=5),
        summarize=False,
        firecrawl_client=http,
    )
    assert result.reviews_written >= 2
    reviews = list_reviews(brand=Brand.MIDNIGHTSATIN, kind=SeoReviewKind.GEO)
    assert len(reviews) >= 2
    plans = list_plans(brand=Brand.MIDNIGHTSATIN, kind=SeoPlanKind.GEO)
    assert len(plans) == 1
    owned = [row for row in list_targets(brand=Brand.MIDNIGHTSATIN) if row.role == SeoTargetRole.OWNED]
    assert owned


def test_aeo_geo_loop_does_not_write_to_customer_sites(aeo_geo_db) -> None:
    """The loop must only read via Firecrawl; no outbound site mutation."""
    calls: list[str] = []

    def tracking_handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url}")
        return _handler(request)

    http = httpx.Client(transport=httpx.MockTransport(tracking_handler))
    run_aeo_geo_loop(
        brand=Brand.MIDNIGHTSATIN,
        budget=SeoBudget(max_targets=1, max_pages_per_target=1, max_minutes=5),
        summarize=False,
        firecrawl_client=http,
    )
    assert calls
    assert all("scrape" in entry.lower() or "/v1/scrape" in entry for entry in calls)
    assert not any(method.startswith("PUT") or method.startswith("POST") and "scrape" not in method for method in calls)
