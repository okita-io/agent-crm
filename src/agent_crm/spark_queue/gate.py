"""Global Spark concurrency gate with FIFO waiting and upstream occupancy checks."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .occupancy import SparkOccupancyClient


class QueueTimeoutError(TimeoutError):
    """Raised when a caller waits too long for a Spark session slot."""


@dataclass
class QueueActorEntry:
    actor: str
    since: float = field(default_factory=time.monotonic)


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
        self._waiting_actors: list[QueueActorEntry] = []
        self._in_flight_actors: list[QueueActorEntry] = []
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
    def observed_upstream_in_flight(self) -> int | None:
        return self._occupancy.last_observed

    @property
    def waiters(self) -> list[QueueActorEntry]:
        return list(self._waiting_actors)

    @property
    def in_flight(self) -> list[QueueActorEntry]:
        return list(self._in_flight_actors)

    async def acquire(self, actor: str | None = None) -> str:
        """Wait in FIFO order until a global Spark session slot is available."""
        actor_label = actor or "unknown"
        async with self._wake:
            self._waiting += 1
            waiter = QueueActorEntry(actor=actor_label)
            self._waiting_actors.append(waiter)
            try:
                deadline = time.monotonic() + self._queue_timeout
                while True:
                    upstream = await self._occupancy.observe_running_count()
                    if upstream is None:
                        # Probe failed (timeout / unreachable). Fail-stale:
                        # hold the last valid observation so a busy GPU is
                        # not mistaken for an idle one; fall back to 0 when
                        # no valid observation exists yet.
                        stale = self._occupancy.last_observed
                        upstream = stale if stale is not None else 0
                    if upstream + self._local_in_flight < self._max:
                        self._local_in_flight += 1
                        self._in_flight_actors.append(QueueActorEntry(actor=actor_label))
                        return actor_label

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
                try:
                    self._waiting_actors.remove(waiter)
                except ValueError:
                    pass

    async def release(self, actor: str | None = None) -> None:
        """Release a local in-flight slot and wake FIFO waiters."""
        actor_label = actor or "unknown"
        async with self._wake:
            if self._local_in_flight > 0:
                self._local_in_flight -= 1
            for index, entry in enumerate(self._in_flight_actors):
                if entry.actor == actor_label:
                    del self._in_flight_actors[index]
                    break
            else:
                if self._in_flight_actors:
                    self._in_flight_actors.pop()
            self._wake.notify_all()
