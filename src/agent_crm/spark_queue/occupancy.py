"""Read Spark SGLang global running-request occupancy from live server signals.

SGLang exposes occupancy through several endpoints. This client tries them in
order and sums per-rank counts when needed. The backend is injectable so tests
can fake Spark already at capacity without a live GPU host.

Verified signal sources (SGLang native API):
- ``GET /v1/loads?include=core`` — structured ``num_running_reqs``
- ``GET /server_info`` — per-DP ``internal_states[].num_running_reqs``
- ``GET /get_server_info`` — deprecated alias of ``/server_info``
- ``GET /metrics`` — Prometheus ``sglang:num_running_reqs`` gauges

The cloud build VM cannot reach ``10.0.1.9``; use ``FakeOccupancyBackend`` in
tests or set ``SPARK_LLM_BASE_URL`` to a reachable mock upstream.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

_RUNNING_REQ_FIELDS = (
    "num_running_reqs",
    "running_reqs",
    "num_running_requests",
    "running_requests",
)

_METRICS_RUNNING_RE = re.compile(
    r"^sglang:num_running_reqs(?:\{[^}]*\})?\s+([0-9]+(?:\.[0-9]+)?)\s*$",
    re.MULTILINE,
)


def _sum_running_fields(payload: Any) -> int | None:
    """Extract a running-request count from a JSON object or list of states."""
    if isinstance(payload, dict):
        for field in _RUNNING_REQ_FIELDS:
            value = payload.get(field)
            if isinstance(value, (int, float)):
                return int(value)
        internal = payload.get("internal_states")
        if isinstance(internal, list):
            return _sum_internal_states(internal)
        core = payload.get("core")
        if isinstance(core, dict):
            return _sum_running_fields(core)
    if isinstance(payload, list):
        return _sum_internal_states(payload)
    return None


def _sum_internal_states(states: list[Any]) -> int | None:
    total = 0
    found = False
    for state in states:
        if not isinstance(state, dict):
            continue
        for field in _RUNNING_REQ_FIELDS:
            value = state.get(field)
            if isinstance(value, (int, float)):
                total += int(value)
                found = True
                break
    return total if found else None


def _parse_metrics_running(text: str) -> int | None:
    total = 0.0
    found = False
    for match in _METRICS_RUNNING_RE.finditer(text):
        total += float(match.group(1))
        found = True
    return int(total) if found else None


class OccupancyBackend(ABC):
    """Pluggable source for Spark's global in-flight request count."""

    @abstractmethod
    async def get_running_count(self) -> int:
        """Return how many requests Spark currently reports as running."""


class SparkOccupancyBackend(OccupancyBackend):
    """Poll Spark SGLang HTTP endpoints for global running-request occupancy."""

    def __init__(self, origin_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._origin = origin_url.rstrip("/")
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))

    async def get_running_count(self) -> int:
        client = await self._get_client()
        owns_client = self._client is None
        try:
            count = await self._read_v1_loads(client)
            if count is not None:
                return count
            count = await self._read_server_info(client, "/server_info")
            if count is not None:
                return count
            count = await self._read_server_info(client, "/get_server_info")
            if count is not None:
                return count
            count = await self._read_metrics(client)
            if count is not None:
                return count
            return 0
        finally:
            if owns_client:
                await client.aclose()

    async def _read_v1_loads(self, client: httpx.AsyncClient) -> int | None:
        response = await client.get(f"{self._origin}/v1/loads", params={"include": "core"})
        if response.status_code != 200:
            return None
        return _sum_running_fields(response.json())

    async def _read_server_info(self, client: httpx.AsyncClient, path: str) -> int | None:
        response = await client.get(f"{self._origin}{path}")
        if response.status_code != 200:
            return None
        return _sum_running_fields(response.json())

    async def _read_metrics(self, client: httpx.AsyncClient) -> int | None:
        response = await client.get(f"{self._origin}/metrics")
        if response.status_code != 200:
            return None
        return _parse_metrics_running(response.text)


class FakeOccupancyBackend(OccupancyBackend):
    """Test double that returns a scripted running count."""

    def __init__(self, running_count: int = 0) -> None:
        self._running_count = running_count

    def set_running_count(self, running_count: int) -> None:
        self._running_count = running_count

    async def get_running_count(self) -> int:
        return self._running_count


class SparkOccupancyClient:
    """Thin wrapper kept for health reporting and gate admission checks."""

    def __init__(self, backend: OccupancyBackend) -> None:
        self._backend = backend
        self._last_observed: int = 0

    @property
    def last_observed(self) -> int:
        return self._last_observed

    async def observe_running_count(self) -> int:
        count = await self._backend.get_running_count()
        self._last_observed = count
        return count

    def set_backend(self, backend: OccupancyBackend) -> None:
        """Replace the occupancy backend (used in tests)."""
        self._backend = backend
