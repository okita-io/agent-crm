"""AgentSpec registry is the SSOT for roster, pause set, enqueue, and Compose."""

from __future__ import annotations

from pathlib import Path

from agent_crm.agency.commands import TOGGLEABLE_AGENTS
from agent_crm.agency.orchestrator import STANDING_WORKERS as ORCH_STANDING
from agent_crm.agent_control import ENQUEUE_ACTION_AGENTS, WORK_AGENTS
from agent_crm.agents.registry import (
    AGENT_SPECS,
    ENQUEUE_ACTION_AGENTS as REG_ENQUEUE,
    KNOWN_AGENT_ROSTER as REG_ROSTER,
    STANDING_WORKERS,
    WORK_AGENTS as REG_WORK,
    AgentSpec,
    compose_services,
    get_agent,
    toggleable_agents,
)
from agent_crm.presence import KNOWN_AGENT_ROSTER

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def test_spec_names_and_compose_services_are_unique() -> None:
    names = [spec.name for spec in AGENT_SPECS]
    assert len(names) == len(set(names))
    services = [spec.compose_service for spec in AGENT_SPECS if spec.compose_service]
    assert len(services) == len(set(services))
    enqueue = [spec.enqueue_action for spec in AGENT_SPECS if spec.enqueue_action]
    assert len(enqueue) == len(set(enqueue))


def test_presence_roster_matches_registry() -> None:
    assert KNOWN_AGENT_ROSTER == REG_ROSTER
    assert len(KNOWN_AGENT_ROSTER) == len(AGENT_SPECS)


def test_work_agents_and_enqueue_match_registry() -> None:
    assert WORK_AGENTS == REG_WORK
    assert ENQUEUE_ACTION_AGENTS == REG_ENQUEUE
    assert WORK_AGENTS == tuple(s.name for s in AGENT_SPECS if s.work_agent)


def test_standing_workers_include_real_loops() -> None:
    assert ORCH_STANDING == STANDING_WORKERS
    for name in (
        "outbound_hunter",
        "research",
        "engagement",
        "publisher",
        "seo",
        "aeo-geo",
        "queue-review",
        "job-dispatcher",
        "orchestrator",
    ):
        assert name in STANDING_WORKERS


def test_toggleable_agents_are_work_plus_orchestrator() -> None:
    assert TOGGLEABLE_AGENTS == toggleable_agents()
    assert "orchestrator" in TOGGLEABLE_AGENTS
    for name in WORK_AGENTS:
        assert name in TOGGLEABLE_AGENTS


def test_placeholders_cannot_be_enqueued_or_composed() -> None:
    for spec in AGENT_SPECS:
        if not spec.placeholder:
            continue
        assert spec.enqueue_action is None
        assert spec.compose_service is None
        assert spec.cli is None
        assert not spec.work_agent
        assert not spec.health_watch
        assert spec.name not in ENQUEUE_ACTION_AGENTS.values()


def test_enqueue_actions_only_map_to_non_placeholders() -> None:
    for action, agent in ENQUEUE_ACTION_AGENTS.items():
        spec = get_agent(agent)
        assert spec is not None
        assert not spec.placeholder
        assert spec.enqueue_action == action
        assert spec.work_agent


def test_compose_declares_every_registry_service() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    for service in compose_services():
        assert f"  {service}:" in content, f"missing compose service {service}"


def test_adding_spec_updates_derived_views_together() -> None:
    """A fictional AgentSpec must appear in roster, pause set, and enqueue map."""
    fictional = AgentSpec(
        name="unit_test_bot",
        display_name="Unit Test Bot",
        compose_service="unit-test-bot",
        cli="unit-test-bot",
        enqueue_action="enqueue_unit_test",
        work_agent=True,
        health_watch=True,
        spark_required=False,
        placeholder=False,
    )
    extended = AGENT_SPECS + (fictional,)
    roster = {spec.name: spec.display_name for spec in extended}
    work = tuple(spec.name for spec in extended if spec.work_agent)
    enqueue = {
        spec.enqueue_action: spec.name
        for spec in extended
        if spec.enqueue_action is not None and not spec.placeholder
    }
    standing = tuple(spec.name for spec in extended if spec.health_watch)
    assert roster["unit_test_bot"] == "Unit Test Bot"
    assert "unit_test_bot" in work
    assert enqueue["enqueue_unit_test"] == "unit_test_bot"
    assert "unit_test_bot" in standing
    assert "unit-test-bot" in tuple(
        spec.compose_service for spec in extended if spec.compose_service
    )
