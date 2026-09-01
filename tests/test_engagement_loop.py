"""Tests for the engagement scan loop (mocked SearXNG/Firecrawl/LLM)."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.engagement_loop import (
    EngagementBudget,
    EngagementLoopResult,
    run_engagement_loop,
    run_engagement_loop_watch,
)
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


def test_engagement_loop_watch_idles_on_empty_queue(loop_db, monkeypatch) -> None:
    from agent_crm import engagement_loop as engagement_loop_mod

    runs = {"n": 0}

    def fake_run(**kwargs):
        runs["n"] += 1
        return EngagementLoopResult(stop_reason="queue_empty")

    def boom(seconds):
        raise KeyboardInterrupt(str(seconds))

    monkeypatch.setattr(engagement_loop_mod, "run_engagement_loop", fake_run)
    monkeypatch.setattr(engagement_loop_mod, "WATCH_POLL_SECONDS", 0.01)
    monkeypatch.setattr(engagement_loop_mod.time, "sleep", boom)
    with pytest.raises(KeyboardInterrupt):
        run_engagement_loop_watch(
            brand=Brand.MIDNIGHTSATIN,
            budget=EngagementBudget(max_venues=1, max_pages_per_venue=1, max_minutes=1),
            summarize=False,
        )
    assert runs["n"] == 1


def test_engagement_loop_watch_continues_when_backlog_remains(loop_db, monkeypatch) -> None:
    from agent_crm import engagement_loop as engagement_loop_mod
    from agent_crm.engagement_query_store import EngagementQueryStore

    store = EngagementQueryStore()
    store.enqueue_query(
        query="site:reddit.com/r/RomanceBooks popular threads",
        brand=Brand.MIDNIGHTSATIN,
        origin="venue:reddit.com",
    )
    runs = {"n": 0}
    sleeps: list[float] = []

    def fake_run(**kwargs):
        runs["n"] += 1
        if runs["n"] == 1:
            return EngagementLoopResult(
                venues_scanned=1,
                stop_reason="max_venues",
            )
        raise KeyboardInterrupt("done")

    def track_sleep(seconds):
        sleeps.append(seconds)
        if runs["n"] >= 2:
            raise KeyboardInterrupt("done")

    monkeypatch.setattr(engagement_loop_mod, "run_engagement_loop", fake_run)
    monkeypatch.setattr(engagement_loop_mod.time, "sleep", track_sleep)
    with pytest.raises(KeyboardInterrupt):
        run_engagement_loop_watch(
            brand=Brand.MIDNIGHTSATIN,
            budget=EngagementBudget(max_venues=1, max_pages_per_venue=1, max_minutes=1),
            summarize=False,
        )
    assert runs["n"] == 2
    assert sleeps and sleeps[0] == 1.0


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
    system_blobs = []
    for call in mock_llm.call_args_list:
        payload = call.args[0]
        for message in payload.get("messages", []):
            if message.get("role") == "system":
                system_blobs.append(message.get("content") or "")
    joined = "\n".join(system_blobs).lower()
    assert "social-media engagement rules" in joined
    assert "helpful first" in joined


def test_engagement_loop_enqueues_follow_ups_and_queue_only_grows(loop_db) -> None:
    from agent_crm.engagement_query_store import EngagementQueryStore

    store_hunt = HuntStore()
    store_hunt.upsert_resource(
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
                        "content": (
                            "Also see r/HistoricalRomance and r/Romance_for_men. "
                            "Galatea spicy romance booktok community."
                        ),
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
                            "500 comments. Cross-post from r/HistoricalRomance. "
                            "Galatea and Radish readers hang out here."
                        ),
                        "metadata": {"title": "Weekly recs megathread — 500 comments"},
                    }
                },
            )
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    queue = EngagementQueryStore()
    before = queue.count_all()
    with patch("agent_crm.engagement_loop.chat_completions") as mock_llm:
        mock_llm.return_value = {
            "choices": [{"message": {"content": '{"should_skip": true}'}}]
        }
        result = run_engagement_loop(
            brand=Brand.MIDNIGHTSATIN,
            budget=EngagementBudget(max_venues=1, max_pages_per_venue=3, max_minutes=5),
            summarize=False,
            searx_client=http,
            firecrawl_client=http,
        )

    after = queue.count_all()
    assert result.venues_scanned >= 1
    assert result.follow_up_terms_enqueued >= 1
    assert after > before
    pending = queue.count_pending(brand=Brand.MIDNIGHTSATIN)
    assert pending >= 1
    combined = " ".join(_engagement_query_texts()).lower()
    assert "historicalromance" in combined or "galatea" in combined or "radish" in combined


def _engagement_query_texts() -> list[str]:
    from sqlalchemy import select

    from agent_crm.db import session_scope
    from agent_crm.models import EngagementQuery

    with session_scope() as session:
        return list(session.scalars(select(EngagementQuery.query)))
