"""Tests for the engagement scan loop (mocked SearXNG/Firecrawl/LLM)."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.engagement_loop import EngagementBudget, run_engagement_loop
from agent_crm.engagement_store import list_drafts, list_threads
from agent_crm.enums import Brand, HuntResourceKind
from agent_crm.hunt_store import HuntStore


@pytest.fixture()
def loop_db(tmp_path, monkeypatch):
    db_path = tmp_path / "engagement-loop.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield db_path
    reset_engine()
    get_settings.cache_clear()


def test_engagement_loop_empty_queue(loop_db) -> None:
    result = run_engagement_loop(brand=Brand.MIDNIGHTSATIN)
    assert result.venues_scanned == 0
    assert result.stop_reason == "queue_empty"
    assert result.drafts_written == 0


def test_engagement_loop_catalogs_hot_thread_and_drafts(loop_db) -> None:
    store = HuntStore()
    store.upsert_resource(
        url="https://www.reddit.com/r/RomanceBooks/",
        brand=Brand.MIDNIGHTSATIN,
        title="Romance Books — 80,000 members",
        found_via_query="romance forums",
        snippet="Most popular romance community. High traffic.",
        kind=HuntResourceKind.COMMUNITY,
    )

    thread_url = "https://www.reddit.com/r/RomanceBooks/comments/abc123/weekly_recs/"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            payload = {
                "results": [
                    {
                        "url": thread_url,
                        "title": "Weekly recs megathread — 500 comments",
                        "content": "Hot trending discussion this week",
                    }
                ]
            }
            return httpx.Response(200, json=payload)
        if request.url.path == "/v1/scrape":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "markdown": (
                            "500 comments. Looking for slow-burn serial romance recs. "
                            "Most popular weekly thread."
                        ),
                        "metadata": {"title": "Weekly recs megathread — 500 comments"},
                    }
                },
            )
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with patch("agent_crm.engagement_loop.chat_completions") as mock_llm:
        mock_llm.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"draft":"If you want a serialized slow-burn, MidnightSatin '
                            'publishes weekly chapters in that lane.","product_angle":'
                            '"serial romance","should_skip":false}'
                        )
                    }
                }
            ]
        }
        result = run_engagement_loop(
            brand=Brand.MIDNIGHTSATIN,
            budget=EngagementBudget(max_venues=2, max_pages_per_venue=3, max_minutes=5),
            searx_client=http,
            firecrawl_client=http,
        )

    assert result.venues_scanned == 1
    assert result.threads_cataloged >= 1
    assert result.drafts_written >= 1
    threads = list_threads(brand=Brand.MIDNIGHTSATIN)
    assert any("comments/abc123" in row.url for row in threads)
    drafts = list_drafts(brand=Brand.MIDNIGHTSATIN)
    assert drafts
    assert "MidnightSatin" in drafts[0].draft_text
    assert drafts[0].status.value == "draft"
