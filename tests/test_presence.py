"""Tests for live agent observer presence merge logic."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_crm.enums import AgentStatus
from agent_crm.presence import (
    HeartbeatSnapshot,
    build_observer_rows,
    external_upstream_slots,
    map_queue_actor_status,
    merge_agent_status,
    spark_slot_summary,
)


def _heartbeat(
    agent_name: str,
    status: AgentStatus,
    *,
    task: str | None = None,
    resource: str | None = None,
) -> HeartbeatSnapshot:
    return HeartbeatSnapshot(
        agent_name=agent_name,
        status=status,
        task=task,
        resource=resource,
        last_seen_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    )


def test_map_queue_actor_status_blocked_for_waiter() -> None:
    assert (
        map_queue_actor_status(
            "lead_scoring",
            waiters=["lead_scoring"],
            in_flight=[],
        )
        == AgentStatus.BLOCKED
    )


def test_map_queue_actor_status_working_for_in_flight() -> None:
    assert (
        map_queue_actor_status(
            "research",
            waiters=[],
            in_flight=["research"],
        )
        == AgentStatus.WORKING
    )


def test_map_queue_actor_status_none_when_absent() -> None:
    assert map_queue_actor_status("nurture", waiters=[], in_flight=[]) is None


def test_merge_agent_status_prefers_blocked_over_thinking() -> None:
    heartbeat = _heartbeat("lead_scoring", AgentStatus.THINKING, task="score lead 42")
    assert (
        merge_agent_status(heartbeat, AgentStatus.BLOCKED) == AgentStatus.BLOCKED
    )


def test_merge_agent_status_prefers_working_over_idle() -> None:
    heartbeat = _heartbeat("research", AgentStatus.IDLE)
    assert merge_agent_status(heartbeat, AgentStatus.WORKING) == AgentStatus.WORKING


def test_build_observer_rows_idle_without_signals() -> None:
    rows = build_observer_rows([], None)
    assert len(rows) == 11
    assert all(row.status == AgentStatus.IDLE for row in rows)
    assert all(row.task is None for row in rows)


def test_build_observer_rows_merges_queue_and_heartbeat() -> None:
    heartbeats = [
        _heartbeat("lead_scoring", AgentStatus.THINKING, task="lead 7"),
    ]
    queue_health = {
        "model": "qwen3.8-27b-sglang",
        "waiters": [{"actor": "lead_scoring"}],
        "in_flight": [{"actor": "research"}],
    }
    rows = {row.name: row for row in build_observer_rows(heartbeats, queue_health)}

    assert rows["lead_scoring"].status == AgentStatus.BLOCKED
    assert rows["lead_scoring"].task == "lead 7"
    assert "waiting" in (rows["lead_scoring"].resource or "")

    assert rows["research"].status == AgentStatus.WORKING
    assert "in-flight" in (rows["research"].resource or "")


def test_external_upstream_slots() -> None:
    assert external_upstream_slots({"observed_upstream_in_flight": 3, "local_in_flight": 1}) == 2
    assert external_upstream_slots(None) == 0


def test_spark_slot_summary_extracts_actor_names() -> None:
    summary = spark_slot_summary(
        {
            "max_concurrency": 4,
            "observed_upstream_in_flight": 2,
            "local_in_flight": 1,
            "waiting": 1,
            "model": "qwen3.8-27b-sglang",
            "waiters": [{"actor": "nurture"}],
            "in_flight": [{"actor": "research"}],
        }
    )
    assert summary["waiters"] == ["nurture"]
    assert summary["in_flight"] == ["research"]
    assert summary["external_upstream_slots"] == 1
