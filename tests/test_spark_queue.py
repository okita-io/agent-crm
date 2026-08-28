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


class _FailingBackend(FakeOccupancyBackend):
    """Backend that always fails the probe (returns None), like a ReadTimeout."""

    def __init__(self) -> None:
        super().__init__(running_count=None)

    async def get_running_count(self) -> int | None:
        return None


@pytest.mark.asyncio
async def test_gate_survives_probe_failure_and_keeps_last_observed() -> None:
    """A timed-out occupancy probe must not raise; gate fails-stale."""
    backend = FakeOccupancyBackend(running_count=4)
    occupancy = SparkOccupancyClient(backend)
    gate = GlobalConcurrencyGate(
        occupancy_client=occupancy,
        max_concurrency=4,
        queue_timeout=0.5,
        poll_interval=0.05,
    )

    # Prime a valid observation, then make the probe fail.
    assert await occupancy.observe_running_count() == 4
    occupancy.set_backend(_FailingBackend())

    start = time.monotonic()
    with pytest.raises(QueueTimeoutError):
        await gate.acquire()
    elapsed = time.monotonic() - start

    # Failed probes kept the last valid observation (4 = full) rather than
    # assuming an idle GPU, and no httpx exception escaped the gate.
    assert elapsed >= 0.4
    assert gate.observed_upstream_in_flight == 4
    assert gate.local_in_flight == 0


def test_usage_prefers_reported_openai_usage() -> None:
    from agent_crm.spark_queue.usage import extract_exchange_tokens

    request = b'{"messages":[{"role":"user","content":"hello there friend"}]}'
    response = (
        b'{"choices":[{"message":{"content":"hi"}}],'
        b'"usage":{"prompt_tokens":12,"completion_tokens":3}}'
    )
    prompt, completion, estimated = extract_exchange_tokens(
        request, response, streamed=False
    )
    assert (prompt, completion, estimated) == (12, 3, False)


def test_usage_estimates_when_upstream_omits_usage() -> None:
    from agent_crm.spark_queue.usage import chars_to_tokens, extract_exchange_tokens

    request = b'{"messages":[{"role":"user","content":"abcdefghijklmnop"}]}'
    response = b'{"choices":[{"message":{"content":"xyzxyzxyzxyz"}}]}'
    prompt, completion, estimated = extract_exchange_tokens(
        request, response, streamed=False
    )
    assert estimated is True
    assert prompt == chars_to_tokens("abcdefghijklmnop")
    assert completion == chars_to_tokens("xyzxyzxyzxyz")


def test_usage_reads_last_sse_usage_chunk() -> None:
    from agent_crm.spark_queue.usage import extract_exchange_tokens

    request = b'{"messages":[{"role":"user","content":"hi"}],"stream":true}'
    stream = (
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n'
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n'
        b'data: {"usage":{"prompt_tokens":9,"completion_tokens":2}}\n'
        b"data: [DONE]\n"
    )
    prompt, completion, estimated = extract_exchange_tokens(
        request, stream, streamed=True
    )
    assert (prompt, completion, estimated) == (9, 2, False)


def test_token_ledger_aggregates_per_actor() -> None:
    from agent_crm.spark_queue.usage import TokenUsageLedger

    ledger = TokenUsageLedger()
    ledger.record("research", 1000, 200)
    ledger.record("research", 500, 50)
    ledger.record("outbound_hunter", 100, 10, estimated=True)
    snapshot = ledger.snapshot()
    assert snapshot["by_actor"]["research"]["prompt_tokens"] == 1500
    assert snapshot["by_actor"]["research"]["completion_tokens"] == 250
    assert snapshot["by_actor"]["research"]["requests"] == 2
    assert snapshot["by_actor"]["research"]["estimated_requests"] == 0
    assert snapshot["by_actor"]["research"]["first_seen_at"]
    assert snapshot["by_actor"]["outbound_hunter"]["estimated_requests"] == 1
    assert snapshot["totals"]["prompt_tokens"] == 1600
    assert snapshot["totals"]["completion_tokens"] == 260
    assert snapshot["totals"]["requests"] == 3


def test_token_ledger_skips_empty_exchanges() -> None:
    from agent_crm.spark_queue.usage import TokenUsageLedger

    ledger = TokenUsageLedger()
    ledger.record("research", 0, 0)
    assert ledger.snapshot()["totals"]["requests"] == 0
