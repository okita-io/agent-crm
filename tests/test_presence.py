"""Tests for live agent observer presence merge logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_crm.config import get_settings
from agent_crm.enums import AgentStatus
from agent_crm.presence import (
    HeartbeatSnapshot,
    KNOWN_AGENT_ROSTER,
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
    rows = build_observer_rows([], None, persisted_usage={}, enabled_by_name={})
    assert len(rows) == len(KNOWN_AGENT_ROSTER)
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
    rows = {
        row.name: row
        for row in build_observer_rows(
            heartbeats, queue_health, persisted_usage={}, enabled_by_name={}
        )
    }

    assert rows["lead_scoring"].status == AgentStatus.BLOCKED
    assert rows["lead_scoring"].task == "lead 7"
    assert "waiting" in (rows["lead_scoring"].resource or "")

    assert rows["research"].status == AgentStatus.WORKING
    assert "in-flight" in (rows["research"].resource or "")


def test_external_upstream_slots() -> None:
    assert external_upstream_slots({"observed_upstream_in_flight": 3, "local_in_flight": 1}) == 2
    assert external_upstream_slots(None) == 0
    assert external_upstream_slots({"observed_upstream_in_flight": None, "local_in_flight": None}) == 0


def test_spark_slot_summary_treats_null_occupancy_as_zero() -> None:
    summary = spark_slot_summary(
        {
            "max_concurrency": None,
            "observed_upstream_in_flight": None,
            "local_in_flight": None,
            "waiting": None,
            "waiters": [],
            "in_flight": [],
        },
        persisted_usage={},
    )
    assert summary["max_concurrency"] == 4
    assert summary["observed_upstream_in_flight"] == 0
    assert summary["local_in_flight"] == 0
    assert summary["waiting"] == 0
    assert summary["external_upstream_slots"] == 0


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
        },
        persisted_usage={},
    )
    assert summary["waiters"] == ["nurture"]
    assert summary["in_flight"] == ["research"]
    assert summary["external_upstream_slots"] == 1
    assert summary["token_usage"]["totals"]["prompt_tokens"] == 0
    assert summary["token_usage"]["input_usd_per_million"] == get_settings().llm_input_usd_per_million
    assert summary["token_usage"]["output_usd_per_million"] == get_settings().llm_output_usd_per_million


def test_spark_slot_summary_does_not_load_token_db_for_occupancy(monkeypatch) -> None:
    def _boom() -> dict:
        raise AssertionError("occupancy summary must not query llm_token_usage")

    monkeypatch.setattr("agent_crm.presence.load_token_usage_snapshot", _boom)
    summary = spark_slot_summary(
        {
            "max_concurrency": 4,
            "observed_upstream_in_flight": 0,
            "local_in_flight": 0,
            "waiting": 0,
            "waiters": [],
            "in_flight": [],
        }
    )
    assert summary["token_usage"]["totals"]["prompt_tokens"] == 0
    from agent_crm.presence import avoided_cloud_usd

    assert avoided_cloud_usd(
        1_000_000,
        1_000_000,
        input_usd_per_million=2.0,
        output_usd_per_million=10.0,
    ) == 12.0
    assert avoided_cloud_usd(
        50_000,
        8_000,
        input_usd_per_million=2.0,
        output_usd_per_million=10.0,
    ) == 0.18


def test_build_observer_rows_includes_token_savings() -> None:
    from agent_crm.presence import avoided_cloud_usd
    from agent_crm.token_usage_store import tokens_per_hour

    first_seen = datetime.now(UTC) - timedelta(hours=2)
    heartbeats = [
        _heartbeat("research", AgentStatus.WORKING, task="page 3"),
    ]
    queue_health = {
        "model": "qwen3.8-27b-sglang",
        "waiters": [],
        "in_flight": [{"actor": "research"}],
    }
    persisted = {
        "by_actor": {
            "research": {
                "prompt_tokens": 50_000,
                "completion_tokens": 8_000,
                "requests": 12,
                "first_seen_at": first_seen.isoformat(),
            },
            "hermes": {
                "prompt_tokens": 1_000_000,
                "completion_tokens": 0,
                "requests": 4,
                "first_seen_at": first_seen.isoformat(),
            },
        }
    }
    rows = {
        row.name: row
        for row in build_observer_rows(
            heartbeats,
            queue_health,
            persisted_usage=persisted,
            enabled_by_name={},
        )
    }
    assert rows["research"].prompt_tokens == 50_000
    assert rows["research"].completion_tokens == 8_000
    assert rows["research"].saved_usd == avoided_cloud_usd(50_000, 8_000)
    assert rows["research"].tokens_per_hour == tokens_per_hour(
        50_000, 8_000, first_seen
    )
    assert "hermes" in rows
    assert rows["hermes"].prompt_tokens == 1_000_000
    assert rows["hermes"].saved_usd == avoided_cloud_usd(1_000_000, 0)


def test_build_observer_rows_includes_enabled_switch() -> None:
    rows = {
        row.name: row
        for row in build_observer_rows(
            [],
            None,
            persisted_usage={},
            enabled_by_name={"research": False, "orchestrator": True},
        )
    }
    assert rows["research"].enabled is False
    assert rows["orchestrator"].enabled is True
    assert rows["outbound_hunter"].enabled is True
    assert "job-dispatcher" in rows


def test_spark_slot_summary_computes_total_savings() -> None:
    from agent_crm.presence import avoided_cloud_usd

    first_seen = datetime.now(UTC) - timedelta(hours=2)
    summary = spark_slot_summary(
        {
            "max_concurrency": 4,
            "observed_upstream_in_flight": 0,
            "local_in_flight": 0,
            "waiting": 0,
        },
        persisted_usage={
            "by_actor": {
                "research": {
                    "prompt_tokens": 1_000_000,
                    "completion_tokens": 200_000,
                    "requests": 40,
                    "first_seen_at": first_seen.isoformat(),
                }
            }
        },
    )
    assert summary["token_usage"]["totals"]["saved_usd"] == avoided_cloud_usd(
        1_000_000, 200_000
    )
    assert summary["token_usage"]["totals"]["tokens_per_hour"] > 0
