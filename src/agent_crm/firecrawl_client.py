"""Tiny Firecrawl scrape client for the Outbound Hunter."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from .config import get_settings


@dataclass(frozen=True)
class ScrapeResult:
    """Normalized scrape output from Firecrawl."""

    url: str
    title: str | None
    markdown: str | None
    metadata: dict[str, Any]


class FirecrawlError(Exception):
    """Firecrawl request failed."""


@lru_cache
def get_firecrawl_base_url() -> str:
    return get_settings().firecrawl_url.rstrip("/")


def scrape(
    url: str,
    *,
    formats: list[str] | None = None,
    timeout: float = 90.0,
    client: httpx.Client | None = None,
) -> ScrapeResult:
    """Scrape a single URL via the ranch Firecrawl API."""
    body = {
        "url": url,
        "formats": formats or ["markdown"],
        "onlyMainContent": True,
    }
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        payload = _post_scrape(http, body)
    except (httpx.HTTPError, ValueError) as exc:
        raise FirecrawlError(f"Firecrawl scrape failed for {url!r}: {exc}") from exc
    finally:
        if owns_client:
            http.close()

    return _normalize_scrape(url, payload)


def _post_scrape(client: httpx.Client, body: dict[str, Any]) -> dict[str, Any]:
    base = get_firecrawl_base_url()
    candidates = (
        f"{base}/v1/scrape",
        f"{base}/v2/scrape",
        f"{base}/scrape",
    )
    last_error: Exception | None = None
    for endpoint in candidates:
        try:
            response = client.post(endpoint, json=body)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                return payload
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise FirecrawlError("No Firecrawl scrape endpoint responded successfully")


def _normalize_scrape(url: str, payload: dict[str, Any]) -> ScrapeResult:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        data = {}

    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    markdown = data.get("markdown")
    if markdown is not None and not isinstance(markdown, str):
        markdown = str(markdown)

    title = metadata.get("title") or data.get("title")
    if title is not None and not isinstance(title, str):
        title = str(title)

    return ScrapeResult(
        url=url,
        title=title,
        markdown=markdown,
        metadata=metadata,
    )
