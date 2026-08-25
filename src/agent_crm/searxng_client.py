"""Tiny SearXNG JSON search client for the Outbound Hunter."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import get_settings


@dataclass(frozen=True)
class SearchResult:
    """One SearXNG hit."""

    url: str
    title: str
    snippet: str


class SearxngError(Exception):
    """SearXNG request failed."""


@lru_cache
def get_searxng_base_url() -> str:
    return get_settings().searxng_url.rstrip("/")


def search(
    query: str,
    *,
    limit: int = 15,
    timeout: float = 30.0,
    client: httpx.Client | None = None,
    categories: str | None = None,
    pageno: int | None = None,
    time_range: str | None = None,
    language: str | None = None,
    engines: str | None = None,
) -> list[SearchResult]:
    """Run a JSON search against the ranch SearXNG instance."""
    params: dict[str, Any] = {"q": query, "format": "json"}
    for key, value in (
        ("categories", categories),
        ("pageno", pageno),
        ("time_range", time_range),
        ("language", language),
        ("engines", engines),
    ):
        if value is not None:
            params[key] = value
    url = f"{get_searxng_base_url()}/search?{urlencode(params)}"

    owns_client = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        response = http.get(url)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SearxngError(f"SearXNG search failed for {query!r}: {exc}") from exc
    finally:
        if owns_client:
            http.close()

    results: list[SearchResult] = []
    for entry in _iter_results(payload):
        url_value = (entry.get("url") or "").strip()
        if not url_value:
            continue
        results.append(
            SearchResult(
                url=url_value,
                title=(entry.get("title") or "").strip(),
                snippet=(entry.get("content") or entry.get("snippet") or "").strip(),
            )
        )
        if len(results) >= limit:
            break
    return results


def _iter_results(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("results")
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]
