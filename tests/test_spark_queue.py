"""Tests for Spark SGLang global occupancy queue."""

from __future__ import annotations

import asyncio
import time

import pytest

from agent_crm.spark_queue.gate import GlobalConcurrencyGate, QueueTimeoutError
from agent_crm.spark_queue.occupancy import (
    FakeOccupancyBackend,
    SparkOccupancyClient,
    _parse_metrics_running,
    _sum_running_fields,
)


def test_sum_running_fields_from_v1_loads_core() -> None:
    payload = {"core": {"num_running_reqs": 3, "num_waiting_reqs": 1}}
    assert _sum_running_fields(payload) == 3


def test_sum_running_fields_from_internal_states() -> None:
    payload = {
        "internal_states": [
            {"num_running_reqs": 2},
            {"num_running_reqs": 1},
        ]
    }
    assert _sum_running_fields(payload) == 3


def test_parse_metrics_running_sums_ranks() -> None:
    text = (
        "# HELP sglang:num_running_reqs The number of running requests.\n"
        "sglang:num_running_reqs{tp_rank=\"0\"} 2.0\n"
        "sglang:num_running_reqs{tp_rank=\"1\"} 1.0\n"
    )
    assert _parse_metrics_running(text) == 3


@pytest.mark.asyncio
async def test_gate_waits_when_upstream_already_at_capacity() -> None:
    backend = FakeOccupancyBackend(running_count=4)
    occupancy = SparkOccupancyClient(backend)
    gate = GlobalConcurrencyGate(
        occupancy_client=occupancy,
        max_concurrency=4,
        queue_timeout=2.0,
        poll_interval=0.05,
    )

    started = asyncio.Event()
    release_upstream = asyncio.Event()

    async def waiter() -> None:
        started.set()
        await gate.acquire()
        await release_upstream.wait()
        await gate.release()

    task = asyncio.create_task(waiter())
    await started.wait()

    # Upstream still full — acquire should not complete yet.
    await asyncio.sleep(0.15)
    assert not task.done()

    backend.set_running_count(3)
    await asyncio.sleep(0.2)
    assert task.done() is False or gate.local_in_flight == 1

    release_upstream.set()
    await task


@pytest.mark.asyncio
async def test_gate_proceeds_when_upstream_occupancy_drops() -> None:
    backend = FakeOccupancyBackend(running_count=4)
    occupancy = SparkOccupancyClient(backend)
    gate = GlobalConcurrencyGate(
        occupancy_client=occupancy,
        max_concurrency=4,
        queue_timeout=5.0,
        poll_interval=0.05,
    )

    acquired = asyncio.Event()

    async def acquire_slot() -> None:
        await gate.acquire()
        acquired.set()
        await gate.release()

    task = asyncio.create_task(acquire_slot())
    await asyncio.sleep(0.15)
    assert not acquired.is_set()

    backend.set_running_count(2)
    await asyncio.wait_for(acquired.wait(), timeout=2.0)
    await task


@pytest.mark.asyncio
async def test_local_in_flight_never_exceeds_max_concurrency() -> None:
    backend = FakeOccupancyBackend(running_count=0)
    occupancy = SparkOccupancyClient(backend)
    max_concurrency = 4
    gate = GlobalConcurrencyGate(
        occupancy_client=occupancy,
        max_concurrency=max_concurrency,
        queue_timeout=5.0,
        poll_interval=0.05,
    )

    peak_local = 0
    lock = asyncio.Lock()
    release_all = asyncio.Event()

    async def worker() -> None:
        await gate.acquire()
        async with lock:
            nonlocal peak_local
            peak_local = max(peak_local, gate.local_in_flight)
        await release_all.wait()
        await gate.release()

    workers = [asyncio.create_task(worker()) for _ in range(5)]
    await asyncio.sleep(0.3)
    async with lock:
        assert peak_local <= max_concurrency

    release_all.set()
    await asyncio.gather(*workers)
    assert gate.local_in_flight == 0


@pytest.mark.asyncio
async def test_fifth_caller_waits_until_slot_frees() -> None:
    backend = FakeOccupancyBackend(running_count=0)
    occupancy = SparkOccupancyClient(backend)
    gate = GlobalConcurrencyGate(
        occupancy_client=occupancy,
        max_concurrency=4,
        queue_timeout=5.0,
        poll_interval=0.05,
    )

    holders: list[asyncio.Task[None]] = []
    fifth_started = asyncio.Event()
    fifth_acquired = asyncio.Event()

    async def hold() -> None:
        await gate.acquire()
        await asyncio.sleep(0.5)
        await gate.release()

    for _ in range(4):
        holders.append(asyncio.create_task(hold()))

    await asyncio.sleep(0.1)
    assert gate.local_in_flight == 4

    async def fifth() -> None:
        fifth_started.set()
        await gate.acquire()
        fifth_acquired.set()
        await gate.release()

    fifth_task = asyncio.create_task(fifth())
    await fifth_started.wait()
    await asyncio.sleep(0.15)
    assert not fifth_acquired.is_set()
    assert gate.waiting >= 1

    await asyncio.gather(*holders)
    await asyncio.wait_for(fifth_acquired.wait(), timeout=2.0)
    await fifth_task


@pytest.mark.asyncio
async def test_upstream_full_blocks_even_with_zero_local_in_flight() -> None:
    """Hermes sessions on Spark count against the cap — CRM must wait."""
    backend = FakeOccupancyBackend(running_count=4)
    occupancy = SparkOccupancyClient(backend)
    gate = GlobalConcurrencyGate(
        occupancy_client=occupancy,
        max_concurrency=4,
        queue_timeout=0.5,
        poll_interval=0.05,
    )

    start = time.monotonic()
    try:
        await gate.acquire()
        pytest.fail("Should not acquire when upstream is already full")
    except QueueTimeoutError as exc:
        assert "No Spark session slot" in str(exc)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.4
    assert gate.local_in_flight == 0


@pytest.mark.asyncio
async def test_gate_tracks_actor_names_for_waiters_and_in_flight() -> None:
    backend = FakeOccupancyBackend(running_count=4)
    occupancy = SparkOccupancyClient(backend)
    gate = GlobalConcurrencyGate(
        occupancy_client=occupancy,
        max_concurrency=4,
        queue_timeout=2.0,
        poll_interval=0.05,
    )

    started = asyncio.Event()
    release_upstream = asyncio.Event()

    async def waiter() -> None:
        started.set()
        await gate.acquire("lead_scoring")
        await release_upstream.wait()
        await gate.release("lead_scoring")

    task = asyncio.create_task(waiter())
    await started.wait()
    await asyncio.sleep(0.1)
    assert any(entry.actor == "lead_scoring" for entry in gate.waiters)

    backend.set_running_count(2)
    await asyncio.sleep(0.2)
    assert any(entry.actor == "lead_scoring" for entry in gate.in_flight)

    release_upstream.set()
    await task
    assert gate.local_in_flight == 0
