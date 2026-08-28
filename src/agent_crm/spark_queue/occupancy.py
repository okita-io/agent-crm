"""Read Spark SGLang global running-request occupancy from live server signals.

SGLang exposes occupancy through several endpoints. This client tries them in
order and sums per-rank counts when needed. The backend is injectable so tests
can fake Spark already at capacity without a live GPU host.

Verified signal sources (SGLang native API):
- ``GET /v1/loads?include=core`` — structured ``num_running_reqs``
- ``GET /server_info`` — per-DP ``internal_states[].num_running_reqs``
- ``GET /get_server_info`` — deprecated alias of ``/server_info``
- ``GET /metrics`` — Prometheus ``sglang:num_running_reqs`` gauges

Probe failures (timeouts, connection errors) are NOT raised: a slow or busy
Spark must never take down a queued request. ``get_running_count`` returns
``None`` when no source answered, and the gate treats that as "unknown —
keep waiting" instead of assuming the GPU is idle.

The cloud build VM cannot reach ``10.0.1.9``; use ``FakeOccupancyBackend`` in
tests or set ``SPARK_LLM_BASE_URL`` to a reachable mock upstream.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)

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
    async def get_running_count(self) -> int | None:
        """Return Spark's running-request count, or ``None`` if unknown.

        ``None`` means "no signal" (probe timed out, all endpoints down, or no
        matching field in the payload). Callers must treat that as unknown,
        not as zero load.
        """

    async def aclose(self) -> None:
        """Release owned resources (default: nothing to close)."""


class SparkOccupancyBackend(OccupancyBackend):
    """Poll Spark SGLang HTTP endpoints for global running-request occupancy."""

    def __init__(self, origin_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._origin = origin_url.rstrip("/")
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.AsyncClient:
        # One persistent pooled client for the process lifetime. Recreating a
        # client (and its TCP connection) on every 250 ms poll caused churn
        # and ReadTimeouts against a busy SGLang server.
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=3.0)
            )
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_running_count(self) -> int | None:
        client = self._get_client()
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
        # No usable signal: report unknown instead of crashing the caller.
        return None

    async def _read_v1_loads(self, client: httpx.AsyncClient) -> int | None:
        try:
            response = await client.get(f"{self._origin}/v1/loads", params={"include": "core"})
        except httpx.HTTPError as exc:
            logger.debug("occupancy /v1/loads probe failed: %s", exc)
            return None
        if response.status_code != 200:
            return None
        return _sum_running_fields(response.json())

    async def _read_server_info(self, client: httpx.AsyncClient, path: str) -> int | None:
        try:
            response = await client.get(f"{self._origin}{path}")
        except httpx.HTTPError as exc:
            logger.debug("occupancy %s probe failed: %s", path, exc)
            return None
        if response.status_code != 200:
            return None
        return _sum_running_fields(response.json())

    async def _read_metrics(self, client: httpx.AsyncClient) -> int | None:
        try:
            response = await client.get(f"{self._origin}/metrics")
        except httpx.HTTPError as exc:
            logger.debug("occupancy /metrics probe failed: %s", exc)
            return None
        if response.status_code != 200:
            return None
        return _parse_metrics_running(response.text)


class FakeOccupancyBackend(OccupancyBackend):
    """Test double with a scripted running count (``None`` = unknown)."""

    def __init__(self, running_count: int | None = 0) -> None:
        self._running_count = running_count

    def set_running_count(self, running_count: int | None) -> None:
        self._running_count = running_count

    async def get_running_count(self) -> int | None:
        return self._running_count


class SparkOccupancyClient:
    """Thin wrapper kept for health reporting and gate admission checks."""

    def __init__(self, backend: OccupancyBackend) -> None:
        self._backend = backend
        self._last_observed: int | None = None

    @property
    def last_observed(self) -> int | None:
        """Last successfully observed upstream count (None = never observed)."""
        return self._last_observed

    async def observe_running_count(self) -> int | None:
        count = await self._backend.get_running_count()
        if count is not None:
            self._last_observed = count
        return count

    def set_backend(self, backend: OccupancyBackend) -> None:
        """Replace the occupancy backend (used in tests)."""
        self._backend = backend

    async def aclose(self) -> None:
        await self._backend.aclose()
