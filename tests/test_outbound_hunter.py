"""Integration tests for outbound hunter loop (mocked externals)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent_crm.enums import Brand
from agent_crm.hunt_store import HuntStore
from agent_crm.outbound_hunter import HuntBudget, OutboundHunter
from agent_crm.searxng_client import SearchResult


class MockSearxng:
    def __init__(self, pages: dict[str, list[SearchResult]] | None = None) -> None:
        self.pages = pages or {}
        self.calls: list[dict] = []

    def search(self, q: str, **params):
        self.calls.append({"q": q, **params})
        return self.pages.get(q, [])


class MockFirecrawl:
    def scrape(self, url: str):
        return None


class MockLlm:
    enabled = False

    def extract_follow_up_terms(self, **kwargs):
        return []


@pytest.fixture()
def hunter(db_url):
    searxng = MockSearxng(
        {
            "seed query": [
                SearchResult(
                    title="Best BookTok Communities",
                    url="https://bookblog.example/communities",
                    content="A list of reader communities",
                ),
                SearchResult(
                    title="Just a moment...",
                    url="https://cf.example/wait",
                    content="",
                ),
                SearchResult(
                    title="Romance Reader Discord List",
                    url="https://discordlist.example/romance",
                    content="discord servers for readers",
                ),
            ],
            "best booktok communities": [
                SearchResult(
                    title="Book Blog Directory",
                    url="https://directory.example/books",
                    content="directory of blogs",
                ),
            ],
        }
    )

    llm = MockLlm()
    llm.extract_follow_up_terms = MagicMock(
        return_value=["best booktok communities"]
    )
    llm.enabled = True

    return OutboundHunter(searxng=searxng, firecrawl=MockFirecrawl(), llm=llm)


def test_resource_upsert_by_url(hunter):
    store = HuntStore()
    row = store.upsert_resource(
        url="https://bookblog.example/communities",
        brand=Brand.MIDNIGHTSATIN,
        title="Best BookTok Communities",
        found_via_query="seed",
        snippet="list",
    )
    assert row is not None
    assert row.hit_count == 1

    row2 = store.upsert_resource(
        url="https://bookblog.example/communities/",
        brand=Brand.MIDNIGHTSATIN,
        title="Best BookTok Communities",
        found_via_query="seed again",
        snippet="list",
    )
    assert row2 is not None
    assert row2.id == row.id
    assert row2.hit_count == 2


def test_junk_url_skipped(hunter):
    store = HuntStore()
    assert store.upsert_resource(
        url="https://site.example/login",
        brand=Brand.MIDNIGHTSATIN,
        title="Login",
        found_via_query="q",
    ) is None


def test_queue_branching_and_dedupe(hunter):
    store = HuntStore()
    assert store.enqueue_query(query="alpha", brand=Brand.MIDNIGHTSATIN, origin="seed")
    assert not store.enqueue_query(query="  ALPHA ", brand=Brand.MIDNIGHTSATIN, origin="seed")
    assert store.enqueue_query(
        query="alpha",
        brand=Brand.MIDNIGHTSATIN,
        origin="seed",
        params={"categories": "news"},
    )


def test_loop_stops_at_max_queries(hunter):
    result = hunter.hunt_loop(
        query="seed query",
        brand=Brand.MIDNIGHTSATIN,
        budget=HuntBudget(max_queries=2, max_minutes=5, max_pages_per_query=0),
        resume=False,
    )
    assert result.queries_run == 2
    assert result.stop_reason == "max_queries"


def test_param_variation_hits_searxng(hunter):
    hunter.hunt_loop(
        query="seed query",
        brand=Brand.MIDNIGHTSATIN,
        budget=HuntBudget(max_queries=3, max_minutes=5, max_pages_per_query=0),
        resume=False,
    )
    assert len(hunter.searxng.calls) >= 2
    categories = [call.get("categories") for call in hunter.searxng.calls]
    assert categories[0] is None
    assert categories[1] == "general"
    assert len(set(categories)) > 1


def test_completed_query_not_searched_again(hunter):
    hunter.hunt_loop(
        query="seed query",
        brand=Brand.MIDNIGHTSATIN,
        budget=HuntBudget(max_queries=1, max_minutes=5, max_pages_per_query=0),
        resume=False,
    )
    assert HuntStore().has_completed_query("seed query", None)
    seed_searches = [c for c in hunter.searxng.calls if c["q"] == "seed query"]
    count_before = len(seed_searches)

    hunter.hunt_loop(
        query="seed query",
        brand=Brand.MIDNIGHTSATIN,
        budget=HuntBudget(max_queries=1, max_minutes=5, max_pages_per_query=0),
        resume=True,
    )
    seed_searches_after = [c for c in hunter.searxng.calls if c["q"] == "seed query"]
    assert len(seed_searches_after) == count_before
