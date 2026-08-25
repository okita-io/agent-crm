"""CRM LLM client helper — always targets the Spark queue, never Spark directly.

Future scoring, nurture, and research agents should use this module so all CRM
LLM traffic flows through the global occupancy-aware queue service.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx

from .config import get_settings


@lru_cache
def get_llm_base_url() -> str:
    """Return the CRM LLM base URL (Spark queue proxy, includes ``/v1``)."""
    return get_settings().llm_base_url.rstrip("/")


def get_llm_client(timeout: float | None = None) -> httpx.Client:
    """Return a sync HTTP client pointed at the Spark queue proxy."""
    return httpx.Client(
        base_url=get_llm_base_url(),
        timeout=timeout or httpx.Timeout(None, connect=30.0),
        follow_redirects=True,
    )


def get_async_llm_client(timeout: float | None = None) -> httpx.AsyncClient:
    """Return an async HTTP client pointed at the Spark queue proxy."""
    return httpx.AsyncClient(
        base_url=get_llm_base_url(),
        timeout=timeout or httpx.Timeout(None, connect=30.0),
        follow_redirects=True,
    )


def chat_completions(payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    """POST ``/chat/completions`` through the Spark queue proxy."""
    with get_llm_client(timeout=timeout) as client:
        response = client.post("/chat/completions", json=payload)
        response.raise_for_status()
        return response.json()
