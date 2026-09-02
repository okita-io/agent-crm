"""Tests for orchestrator command execution (no LLM)."""

from __future__ import annotations

import httpx

from agent_crm.agency_commands import execute_action, execute_actions
from agent_crm.agent_control import is_agent_enabled, set_agent_enabled
from agent_crm.enums import Brand
from agent_crm.hunt_store import HuntStore


def test_execute_set_agent_enabled(db_url) -> None:
    result = execute_action(
        {"type": "set_agent_enabled", "agent": "research", "enabled": False}
    )
    assert result["ok"] is True
    assert result["enabled"] is False
    assert is_agent_enabled("research") is False


def test_execute_enqueue_hunt(db_url) -> None:
    result = execute_action(
        {
            "type": "enqueue_hunt",
            "query": "astrology community reddit",
            "brand": Brand.TACTIC_STUDIO.value,
        }
    )
    assert result["ok"] is True
    assert result["enqueued"] is True
    assert HuntStore().count_pending() >= 1


def test_execute_unknown_agent_fails(db_url) -> None:
    result = execute_action(
        {"type": "set_agent_enabled", "agent": "nurture", "enabled": False}
    )
    assert result["ok"] is False


def test_execute_actions_batch(db_url) -> None:
    results = execute_actions(
        [
            {"type": "set_agent_enabled", "agent": "seo", "enabled": False},
            {
                "type": "enqueue_research",
                "query": "competitor sites",
                "brand": "midnightsatin",
                "kind": "competitor",
            },
        ]
    )
    assert len(results) == 2
    assert results[0]["ok"] is True
    assert results[1]["ok"] is True


def test_try_rule_based_toggle_pause_research(db_url) -> None:
    from agent_crm.agency_commands import try_rule_based_toggle

    result = try_rule_based_toggle("please pause research")
    assert result is not None
    reply, actions, results = result
    assert "Research" in reply
    assert actions == [{"type": "set_agent_enabled", "agent": "research", "enabled": False}]
    assert results[0]["ok"] is True
    assert is_agent_enabled("research") is False


def test_try_rule_based_toggle_hunter_alias(db_url) -> None:
    from agent_crm.agency_commands import try_rule_based_toggle

    result = try_rule_based_toggle("turn on the hunter")
    assert result is not None
    reply, _, results = result
    assert "Outbound Hunter" in reply
    assert results[0]["agent"] == "outbound_hunter"
    assert results[0]["enabled"] is True


def test_format_llm_error_upstream(db_url) -> None:
    from agent_crm.agency_commands import format_llm_error

    request = httpx.Request("POST", "http://spark-queue:8088/v1/chat/completions")
    response = httpx.Response(503, request=request)
    err = httpx.HTTPStatusError("upstream", request=request, response=response)
    text = format_llm_error(err)
    assert "Spark LLM is unavailable" in text
    assert "Pause/resume" in text


def test_enqueue_skipped_when_agent_paused(db_url) -> None:
    set_agent_enabled("outbound_hunter", False)
    result = execute_action(
        {
            "type": "enqueue_hunt",
            "query": "astrology community reddit",
            "brand": Brand.TACTIC_STUDIO.value,
        }
    )
    assert result["ok"] is False
    assert result["agent"] == "outbound_hunter"
    assert "paused" in result["detail"]
    assert HuntStore().count_pending() == 0


def test_execute_actions_enables_then_enqueues(db_url) -> None:
    set_agent_enabled("research", False)
    results = execute_actions(
        [
            {
                "type": "enqueue_research",
                "query": "competitor sites",
                "brand": "midnightsatin",
                "kind": "competitor",
            },
            {"type": "set_agent_enabled", "agent": "research", "enabled": True},
        ]
    )
    assert results[1]["ok"] is True
    assert results[1]["enabled"] is True
    assert results[0]["ok"] is True
    assert results[0]["enqueued"] is True


def test_prompt_focuses_on_enabled_agents(db_url) -> None:
    from agent_crm.agency_commands import _build_system_prompt, _operator_context
    from agent_crm.agent_control import WORK_AGENTS

    for name in WORK_AGENTS:
        set_agent_enabled(name, name in {"outbound_hunter", "research"})

    ctx = _operator_context()
    assert ctx["focused"] is True
    assert ctx["allowed_enqueue_actions"] == ["enqueue_hunt", "enqueue_research"]
    prompt = _build_system_prompt(ctx)
    assert "Focused roster" in prompt
    assert "enqueue_hunt, enqueue_research" in prompt
    assert "Do not enqueue work for paused agents" in prompt
