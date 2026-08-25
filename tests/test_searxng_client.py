"""Tests for SearXNG client param forwarding and pagination."""

from __future__ import annotations

import httpx

from agent_crm.searxng_client import search


def test_search_forwards_searxng_params() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json={"results": []})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    search(
        "booktok communities",
        limit=5,
        client=http,
        categories="social media",
        pageno=2,
        time_range="year",
        language="en",
        engines="google",
    )
    assert captured["q"] == "booktok communities"
    assert captured["format"] == "json"
    assert captured["categories"] == "social media"
    assert captured["pageno"] == "2"
    assert captured["time_range"] == "year"
    assert captured["language"] == "en"
    assert captured["engines"] == "google"


def test_search_paginates_until_limit() -> None:
    page_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page_calls.append(dict(request.url.params).get("pageno", "1"))
        page = int(dict(request.url.params).get("pageno", "1"))
        results = [
            {
                "url": f"https://example.com/page{page}-a",
                "title": f"Page {page} A",
                "content": "alpha",
            },
            {
                "url": f"https://example.com/page{page}-b",
                "title": f"Page {page} B",
                "content": "beta",
            },
        ]
        return httpx.Response(200, json={"results": results})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    results = search("boutique studio", limit=5, client=http)
    assert len(results) == 5
    assert page_calls == ["1", "2", "3"]
    urls = {hit.url for hit in results}
    assert len(urls) == 5


def test_search_paginates_stops_on_empty_page() -> None:
    page_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(dict(request.url.params).get("pageno", "1"))
        page_calls.append(str(page))
        if page == 1:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.com/only",
                            "title": "Only",
                            "content": "one",
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"results": []})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    results = search("sparse query", limit=50, client=http)
    assert len(results) == 1
    assert page_calls == ["1", "2"]


def test_search_dedupes_urls_across_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(dict(request.url.params).get("pageno", "1"))
        if page == 1:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.com/shared",
                            "title": "Shared",
                            "content": "first",
                        },
                        {
                            "url": "https://example.com/unique-1",
                            "title": "Unique 1",
                            "content": "one",
                        },
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com/shared",
                        "title": "Shared again",
                        "content": "dup",
                    },
                    {
                        "url": "https://example.com/unique-2",
                        "title": "Unique 2",
                        "content": "two",
                    },
                ]
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    results = search("dedupe query", limit=10, client=http)
    assert len(results) == 3
    assert results[0].url == "https://example.com/shared"
    assert results[1].url == "https://example.com/unique-1"
    assert results[2].url == "https://example.com/unique-2"
