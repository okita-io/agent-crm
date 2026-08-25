"""Tests for bounded hunt loop (mocked SearXNG/Firecrawl/LLM)."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import Brand, HuntResourceKind
from agent_crm.hunt_loop import HuntBudget, run_hunt_loop
from agent_crm.hunt_store import HuntStore
from agent_crm.searxng_client import SearchResult


def _searx_transport(pages: dict[str, list[SearchResult]], calls: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            params = dict(request.url.params)
            calls.append(params)
            query = params.get("q", "")
            payload = {
                "results": [
                    {"url": hit.url, "title": hit.title, "content": hit.snippet}
                    for hit in pages.get(query, [])
                ]
            }
            return httpx.Response(200, json=payload)
        if request.url.path == "/v1/scrape":
            return httpx.Response(
                200,
                json={"data": {"markdown": "body", "metadata": {"title": "Page"}}},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture()
def loop_db(tmp_path, monkeypatch):
    db_path = tmp_path / "loop.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield db_path
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture()
def mock_pages() -> dict[str, list[SearchResult]]:
    return {
        "seed query": [
            SearchResult(
                title="Best BookTok Communities",
                url="https://bookblog.example/communities",
                snippet="A list of reader communities",
            ),
            SearchResult(
                title="Just a moment...",
                url="https://cf.example/wait",
                snippet="",
            ),
        ],
        "best booktok communities": [
            SearchResult(
                title="Book Blog Directory",
                url="https://directory.example/books",
                snippet="directory of blogs",
            ),
        ],
    }


def test_resource_upsert_by_url(loop_db):
    store = HuntStore()
    row = store.upsert_resource(
        url="https://bookblog.example/communities",
        brand=Brand.MIDNIGHTSATIN,
        title="Best BookTok Communities",
        found_via_query="seed",
        snippet="list",
    )
    assert row.resource is not None
    row2 = store.upsert_resource(
        url="https://bookblog.example/communities/",
        brand=Brand.MIDNIGHTSATIN,
        title="Best BookTok Communities",
        found_via_query="seed again",
        snippet="list",
    )
    assert row2.resource is not None
    assert row2.resource.id == row.resource.id
    assert row2.resource.hit_count == 2
    assert row2.is_new is False


def test_junk_url_skipped(loop_db):
    assert (
        HuntStore().upsert_resource(
            url="https://site.example/login",
            brand=Brand.MIDNIGHTSATIN,
            title="Login",
            found_via_query="q",
        ).resource
        is None
    )


def test_queue_branching_and_dedupe(loop_db):
    store = HuntStore()
    assert store.enqueue_query(query="alpha", brand=Brand.MIDNIGHTSATIN, origin="seed")
    assert not store.enqueue_query(query="  ALPHA ", brand=Brand.MIDNIGHTSATIN, origin="seed")
    assert store.enqueue_query(
        query="alpha",
        brand=Brand.MIDNIGHTSATIN,
        origin="seed",
        params={"categories": "news"},
    )


def test_loop_stops_at_max_queries(loop_db, mock_pages):
    calls: list[dict] = []
    http = httpx.Client(transport=_searx_transport(mock_pages, calls))
    with patch("agent_crm.hunt_loop.chat_completions") as mock_llm:
        mock_llm.return_value = {
            "choices": [{"message": {"content": '{"terms": ["best booktok communities"]}'}}]
        }
        result = run_hunt_loop(
            query="seed query",
            brand=Brand.MIDNIGHTSATIN,
            budget=HuntBudget(max_queries=2, max_minutes=5, max_pages_per_query=0),
            resume=False,
            searx_client=http,
            firecrawl_client=http,
        )
    assert result.queries_run == 2
    assert result.stop_reason == "max_queries"


def test_param_variation_hits_searxng(loop_db, mock_pages):
    calls: list[dict] = []
    http = httpx.Client(transport=_searx_transport(mock_pages, calls))
    with patch("agent_crm.hunt_loop.chat_completions") as mock_llm:
        mock_llm.return_value = {
            "choices": [{"message": {"content": '{"terms": ["best booktok communities"]}'}}]
        }
        run_hunt_loop(
            query="seed query",
            brand=Brand.MIDNIGHTSATIN,
            budget=HuntBudget(max_queries=3, max_minutes=5, max_pages_per_query=0),
            resume=False,
            searx_client=http,
            firecrawl_client=http,
        )
    assert len(calls) >= 2
    first_page_calls = [call for call in calls if call.get("pageno") in (None, "1")]
    categories = [call.get("categories") for call in first_page_calls]
    assert categories[0] is None
    assert categories[1] == "general"


def test_loop_scrapes_beyond_legacy_eight_page_cap(loop_db):
    """Hunt loop should scrape up to max_pages_per_query, not a hard top-8 slice."""
    pages: dict[str, list[SearchResult]] = {
        "wide query": [
            SearchResult(
                title=f"Resource {idx}",
                url=f"https://resource{idx}.example",
                snippet=f"snippet {idx}",
            )
            for idx in range(12)
        ],
    }
    calls: list[dict] = []
    scrape_calls = 0

    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            params = dict(request.url.params)
            calls.append(params)
            query = params.get("q", "")
            payload = {
                "results": [
                    {"url": hit.url, "title": hit.title, "content": hit.snippet}
                    for hit in pages.get(query, [])
                ]
            }
            return httpx.Response(200, json=payload)
        if request.url.path == "/v1/scrape":
            nonlocal scrape_calls
            scrape_calls += 1
            return httpx.Response(
                200,
                json={"data": {"markdown": "body", "metadata": {"title": "Page"}}},
            )
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(transport))
    with patch("agent_crm.hunt_loop.chat_completions") as mock_llm:
        mock_llm.return_value = {"choices": [{"message": {"content": '{"terms": []}'}}]}
        result = run_hunt_loop(
            query="wide query",
            brand=Brand.MIDNIGHTSATIN,
            budget=HuntBudget(max_queries=1, max_minutes=5, max_pages_per_query=10),
            resume=False,
            searx_client=http,
            firecrawl_client=http,
        )

    assert result.queries_run == 1
    assert scrape_calls == 10


def test_completed_query_not_searched_again(loop_db, mock_pages):
    calls: list[dict] = []
    http = httpx.Client(transport=_searx_transport(mock_pages, calls))
    with patch("agent_crm.hunt_loop.chat_completions") as mock_llm:
        mock_llm.return_value = {"choices": [{"message": {"content": '{"terms": []}'}}]}
        run_hunt_loop(
            query="seed query",
            brand=Brand.MIDNIGHTSATIN,
            budget=HuntBudget(max_queries=1, max_minutes=5, max_pages_per_query=0),
            resume=False,
            searx_client=http,
            firecrawl_client=http,
        )
    count_before = len([c for c in calls if c.get("q") == "seed query"])
    with patch("agent_crm.hunt_loop.chat_completions") as mock_llm:
        mock_llm.return_value = {"choices": [{"message": {"content": '{"terms": []}'}}]}
        run_hunt_loop(
            query="seed query",
            brand=Brand.MIDNIGHTSATIN,
            budget=HuntBudget(max_queries=1, max_minutes=5, max_pages_per_query=0),
            resume=True,
            searx_client=http,
            firecrawl_client=http,
        )
    count_after = len([c for c in calls if c.get("q") == "seed query"])
    assert count_after == count_before


def test_community_and_person_feedback_enqueue(loop_db, monkeypatch):
    """Reddit community + extracted contact name feed back into hunt_queries."""
    monkeypatch.setenv("CRM_HUNTER_COMMUNITY_TERMS_PER_RUN", "30")
    monkeypatch.setenv("CRM_HUNTER_PERSON_TERMS_PER_RUN", "20")
    get_settings.cache_clear()

    reddit_url = "https://www.reddit.com/r/RomanceBooks/"
    pages: dict[str, list[SearchResult]] = {
        "romance reader communities": [
            SearchResult(
                title="Romance Books on Reddit",
                url=reddit_url,
                snippet="A subreddit for romance readers",
            ),
        ],
    }
    calls: list[dict] = []

    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            params = dict(request.url.params)
            calls.append(params)
            query = params.get("q", "")
            payload = {
                "results": [
                    {"url": hit.url, "title": hit.title, "content": hit.snippet}
                    for hit in pages.get(query, [])
                ]
            }
            return httpx.Response(200, json=payload)
        if request.url.path == "/v1/scrape":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "markdown": "Ada Vega <ada.vega@romancebooks.test> moderates the community.",
                        "metadata": {"title": "Romance Books subreddit"},
                    }
                },
            )
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(transport))
    with patch("agent_crm.hunt_loop.chat_completions") as mock_llm:
        mock_llm.return_value = {"choices": [{"message": {"content": '{"terms": []}'}}]}
        result = run_hunt_loop(
            query="romance reader communities",
            brand=Brand.MIDNIGHTSATIN,
            budget=HuntBudget(max_queries=1, max_minutes=5, max_pages_per_query=1),
            resume=False,
            searx_client=http,
            firecrawl_client=http,
        )

    store = HuntStore()
    resources = store.list_resources(brand=Brand.MIDNIGHTSATIN)
    reddit_resource = next(
        (row for row in resources if "/r/RomanceBooks" in row.url or "/r/romancebooks" in row.url.lower()),
        None,
    )
    assert reddit_resource is not None
    assert reddit_resource.kind == HuntResourceKind.COMMUNITY

    feedback = store.list_feedback_queries(brand=Brand.MIDNIGHTSATIN, limit=100)
    community_queries = [row for row in feedback if row.origin.startswith("community:")]
    person_queries = [row for row in feedback if row.origin.startswith("person:")]

    assert result.community_terms_enqueued >= 2
    assert result.person_terms_enqueued >= 3
    assert len(community_queries) >= 2
    assert len(person_queries) >= 3
    assert any("site:reddit.com/r/RomanceBooks" in row.query for row in community_queries)
    assert any("Ada Vega" in row.query for row in person_queries)
    assert all("@" not in row.query for row in feedback)
    assert all("invented" not in row.query.lower() for row in feedback)
    assert not any(row.query.lower().startswith("ada.vega@") for row in feedback)

