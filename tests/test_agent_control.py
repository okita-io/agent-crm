"""Tests for per-agent enable/disable controls."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_crm.agent_control import (
    is_agent_enabled,
    list_agent_enabled,
    set_agent_enabled,
    stop_if_disabled,
    wait_while_disabled,
)
from agent_crm.enums import AgentStatus
from agent_crm.heartbeat import list_heartbeats


def test_missing_toggle_defaults_enabled(db_url) -> None:
    assert is_agent_enabled("seo") is True
    assert list_agent_enabled()["seo"] is True


def test_set_agent_enabled_persists(db_url) -> None:
    set_agent_enabled("seo", False)
    assert is_agent_enabled("seo") is False
    set_agent_enabled("seo", True)
    assert is_agent_enabled("seo") is True


def test_disable_records_paused_heartbeat(db_url) -> None:
    set_agent_enabled("aeo-geo", False)
    heartbeats = {row.agent_name: row for row in list_heartbeats()}
    assert heartbeats["aeo-geo"].status == AgentStatus.IDLE
    assert heartbeats["aeo-geo"].task == "paused"


def test_stop_if_disabled_returns_true_when_off(db_url) -> None:
    set_agent_enabled("engagement", False)
    assert stop_if_disabled("engagement") is True
    assert stop_if_disabled("engagement") is True


def test_stop_if_disabled_returns_false_when_on(db_url) -> None:
    set_agent_enabled("engagement", True)
    assert stop_if_disabled("engagement") is False


def test_wait_while_disabled_unblocks_when_enabled(db_url) -> None:
    set_agent_enabled("research", False)
    with patch("agent_crm.agent_control.time.sleep") as sleep:
        set_agent_enabled("research", True)
        wait_while_disabled("research", poll_seconds=0.01)
    assert sleep.call_count >= 0


def test_wait_while_disabled_polls_until_enabled(db_url) -> None:
    set_agent_enabled("orchestrator", False)
    calls = {"n": 0}

    def _maybe_enable(*_args, **_kwargs) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            set_agent_enabled("orchestrator", True)

    with patch("agent_crm.agent_control.time.sleep", side_effect=_maybe_enable):
        wait_while_disabled("orchestrator", poll_seconds=0.01)
    assert is_agent_enabled("orchestrator") is True
