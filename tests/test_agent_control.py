"""Tests for Live Agents enable/disable switches."""

from __future__ import annotations

from agent_crm.agent_control import (
    PAUSED_TASK,
    is_agent_enabled,
    list_agent_enabled,
    set_agent_enabled,
    stop_if_disabled,
)
from agent_crm.enums import AgentStatus
from agent_crm.heartbeat import list_heartbeats
from agent_crm.hunt_loop import HuntBudget, run_hunt_loop


def test_missing_toggle_defaults_to_enabled(db_url) -> None:
    assert is_agent_enabled("outbound_hunter") is True
    assert list_agent_enabled() == {}


def test_set_agent_enabled_round_trip(db_url) -> None:
    assert set_agent_enabled("research", False) is False
    assert is_agent_enabled("research") is False
    assert list_agent_enabled()["research"] is False
    heartbeats = {row.agent_name: row for row in list_heartbeats()}
    assert heartbeats["research"].status == AgentStatus.IDLE
    assert heartbeats["research"].task == PAUSED_TASK

    assert set_agent_enabled("research", True) is True
    assert is_agent_enabled("research") is True


def test_stop_if_disabled(db_url) -> None:
    assert stop_if_disabled("seo") is False
    set_agent_enabled("seo", False)
    assert stop_if_disabled("seo") is True


def test_hunt_loop_returns_disabled_without_searching(db_url) -> None:
    set_agent_enabled("outbound_hunter", False)
    result = run_hunt_loop(
        query="seed query",
        budget=HuntBudget(max_queries=2, max_minutes=1, max_pages_per_query=1),
    )
    assert result.stop_reason == "disabled"
    assert result.queries_run == 0
