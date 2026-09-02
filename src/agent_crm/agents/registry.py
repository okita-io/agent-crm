"""Single source of truth for standing-agent identity.

Roster display names, Live Agents pause set, Command enqueue maps,
orchestrator health watches, and Compose service assertions all derive
from ``AGENT_SPECS``. Docker Compose YAML stays hand-written; tests fail
when a registry ``compose_service`` is missing from the file.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    """Identity and capability flags for one roster / worker agent."""

    name: str
    display_name: str
    compose_service: str | None = None
    cli: str | None = None
    enqueue_action: str | None = None
    work_agent: bool = False
    health_watch: bool = False
    spark_required: bool = False
    placeholder: bool = False


AGENT_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec(
        name="lead_intake",
        display_name="Lead Intake",
        placeholder=True,
    ),
    AgentSpec(
        name="lead_scoring",
        display_name="Lead Scoring",
        placeholder=True,
    ),
    AgentSpec(
        name="research",
        display_name="Research",
        compose_service="research-loop",
        cli="research-loop",
        enqueue_action="enqueue_research",
        work_agent=True,
        health_watch=True,
        spark_required=True,
    ),
    AgentSpec(
        name="outbound_hunter",
        display_name="Outbound Hunter",
        compose_service="hunt-loop",
        cli="hunt-loop",
        enqueue_action="enqueue_hunt",
        work_agent=True,
        health_watch=True,
        spark_required=True,
    ),
    AgentSpec(
        name="engagement",
        display_name="Agent Engagement",
        compose_service="engagement-loop",
        cli="engagement-loop",
        enqueue_action="enqueue_engagement",
        work_agent=True,
        health_watch=True,
        spark_required=True,
    ),
    AgentSpec(
        name="seo",
        display_name="SEO Documents",
        compose_service="seo-loop",
        cli="seo-loop",
        enqueue_action="enqueue_seo",
        work_agent=True,
        health_watch=True,
        spark_required=True,
    ),
    AgentSpec(
        name="aeo-geo",
        display_name="AEO / GEO Documents",
        compose_service="aeo-geo-loop",
        cli="aeo-geo-loop",
        enqueue_action="enqueue_aeo_geo",
        work_agent=True,
        health_watch=True,
        spark_required=True,
    ),
    AgentSpec(
        name="queue-review",
        display_name="Queue Review",
        compose_service="queue-review",
        cli="queue-review",
        work_agent=True,
        health_watch=True,
        spark_required=True,
    ),
    AgentSpec(
        name="outreach_writer",
        display_name="Outreach Writer",
        placeholder=True,
    ),
    AgentSpec(
        name="nurture",
        display_name="Nurture",
        placeholder=True,
    ),
    AgentSpec(
        name="crm_manager",
        display_name="CRM Manager",
        placeholder=True,
    ),
    AgentSpec(
        name="analytics",
        display_name="Analytics",
        placeholder=True,
    ),
    AgentSpec(
        name="brand_router",
        display_name="Brand Router",
        placeholder=True,
    ),
    AgentSpec(
        name="lead_verifier",
        display_name="Lead Verifier",
        placeholder=True,
    ),
    AgentSpec(
        name="job-dispatcher",
        display_name="Job Dispatcher",
        compose_service="contact-worker",
        cli="jobs",
        work_agent=True,
        health_watch=True,
        spark_required=False,
    ),
    AgentSpec(
        name="orchestrator",
        display_name="Orchestrator",
        compose_service="orchestrator",
        cli="orchestrate",
        work_agent=False,
        health_watch=True,
        spark_required=False,
    ),
)


def get_agent(name: str) -> AgentSpec | None:
    """Return the spec for ``name``, or ``None`` when unknown."""
    for spec in AGENT_SPECS:
        if spec.name == name:
            return spec
    return None


def compose_services() -> tuple[str, ...]:
    """Compose service names declared by non-placeholder standing agents."""
    return tuple(
        spec.compose_service
        for spec in AGENT_SPECS
        if spec.compose_service is not None
    )


def toggleable_agents() -> tuple[str, ...]:
    """Agents the Command pane may pause/resume (work agents + orchestrator)."""
    return tuple(
        spec.name
        for spec in AGENT_SPECS
        if spec.work_agent or spec.name == "orchestrator"
    )


KNOWN_AGENT_ROSTER: dict[str, str] = {
    spec.name: spec.display_name for spec in AGENT_SPECS
}

WORK_AGENTS: tuple[str, ...] = tuple(
    spec.name for spec in AGENT_SPECS if spec.work_agent
)

ENQUEUE_ACTION_AGENTS: dict[str, str] = {
    spec.enqueue_action: spec.name
    for spec in AGENT_SPECS
    if spec.enqueue_action is not None and not spec.placeholder
}

STANDING_WORKERS: tuple[str, ...] = tuple(
    spec.name for spec in AGENT_SPECS if spec.health_watch
)
