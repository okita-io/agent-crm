"""Tests for SearXNG client param forwarding."""

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
