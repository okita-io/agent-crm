"""Firecrawl scrape client."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from agent_crm.config import Settings, get_settings


@dataclass
class ScrapeResult:
    url: str
    title: str | None
    markdown: str | None
    metadata: dict | None = None


class FirecrawlClient:
    """Thin wrapper around the local Firecrawl scrape endpoint."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.firecrawl_base_url.rstrip("/")

    def scrape(self, url: str) -> ScrapeResult | None:
        """Scrape a single page. Returns None on failure."""
        payload = {"url": url, "formats": ["markdown"]}
        try:
            with httpx.Client(timeout=self.settings.hunter_request_timeout) as client:
                response = client.post(f"{self.base_url}/v1/scrape", json=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        data = body.get("data") or body
        metadata = data.get("metadata") or {}
        return ScrapeResult(
            url=url,
            title=metadata.get("title") or data.get("title"),
            markdown=data.get("markdown"),
            metadata=metadata,
        )
