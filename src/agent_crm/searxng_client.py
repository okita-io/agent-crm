"""SearXNG search client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from agent_crm.config import Settings, get_settings


@dataclass
class SearchResult:
    title: str
    url: str
    content: str | None = None
    engine: str | None = None


class SearxngClient:
    """Thin wrapper around the SearXNG JSON API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.searxng_base_url.rstrip("/")

    def search(self, q: str, **params: Any) -> list[SearchResult]:
        """Run a search, forwarding any supported SearXNG params."""
        query_params: dict[str, Any] = {"q": q, "format": "json"}
        for key, value in params.items():
            if value is not None:
                query_params[key] = value

        with httpx.Client(timeout=self.settings.hunter_request_timeout) as client:
            response = client.get(f"{self.base_url}/search", params=query_params)
            response.raise_for_status()
            payload = response.json()

        results: list[SearchResult] = []
        for item in payload.get("results", []):
            url = item.get("url")
            if not url:
                continue
            results.append(
                SearchResult(
                    title=item.get("title") or "",
                    url=url,
                    content=item.get("content"),
                    engine=item.get("engine"),
                )
            )
        return results

    def last_request_params(self, q: str, **params: Any) -> dict[str, Any]:
        """Expose the params that would be sent (used by tests)."""
        query_params: dict[str, Any] = {"q": q, "format": "json"}
        for key, value in params.items():
            if value is not None:
                query_params[key] = value
        return query_params
