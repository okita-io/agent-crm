"""Global Spark concurrency gate with FIFO waiting and upstream occupancy checks."""

from __future__ import annotations

import asyncio
import time

from .occupancy import SparkOccupancyClient


class QueueTimeoutError(TimeoutError):
    """Raised when a caller waits too long for a Spark session slot."""


class GlobalConcurrencyGate:
    """Admit CRM-originated work only when Spark has a free session slot.

    Hermes and other ranch agents hit Spark directly; their sessions appear in
    Spark's reported occupancy. This gate polls that occupancy and only dispatches
    when ``observed_upstream + local_in_flight < max_concurrency``.
    """

    def __init__(
        self,
        occupancy_client: SparkOccupancyClient,
        max_concurrency: int,
        queue_timeout: float,
        poll_interval: float,
    ) -> None:
        self._occupancy = occupancy_client
        self._max = max_concurrency
        self._queue_timeout = queue_timeout
        self._poll_interval = poll_interval
        self._local_in_flight = 0
        self._waiting = 0
        self._lock = asyncio.Lock()
        self._wake = asyncio.Condition(self._lock)

    @property
    def max_concurrency(self) -> int:
        return self._max

    @property
    def local_in_flight(self) -> int:
        return self._local_in_flight

    @property
    def waiting(self) -> int:
        return self._waiting

    @property
    def observed_upstream_in_flight(self) -> int:
        return self._occupancy.last_observed

    async def acquire(self) -> None:
        """Wait in FIFO order until a global Spark session slot is available."""
        async with self._wake:
            self._waiting += 1
            try:
                deadline = time.monotonic() + self._queue_timeout
                while True:
                    upstream = await self._occupancy.observe_running_count()
                    if upstream + self._local_in_flight < self._max:
                        self._local_in_flight += 1
                        return

                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise QueueTimeoutError(
                            f"No Spark session slot within {self._queue_timeout}s "
                            f"(upstream={upstream}, local={self._local_in_flight}, "
                            f"max={self._max})"
                        )
                    wait_for = min(self._poll_interval, remaining)
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=wait_for)
                    except TimeoutError:
                        pass
            finally:
                self._waiting -= 1

    async def release(self) -> None:
        """Release a local in-flight slot and wake FIFO waiters."""
        async with self._wake:
            if self._local_in_flight > 0:
                self._local_in_flight -= 1
            self._wake.notify_all()
