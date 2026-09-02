"""Standing-agent identity and derived roster views."""

from .registry import (
    AGENT_SPECS,
    ENQUEUE_ACTION_AGENTS,
    KNOWN_AGENT_ROSTER,
    STANDING_WORKERS,
    WORK_AGENTS,
    AgentSpec,
    compose_services,
    get_agent,
    toggleable_agents,
)

__all__ = [
    "AGENT_SPECS",
    "ENQUEUE_ACTION_AGENTS",
    "KNOWN_AGENT_ROSTER",
    "STANDING_WORKERS",
    "WORK_AGENTS",
    "AgentSpec",
    "compose_services",
    "get_agent",
    "toggleable_agents",
]
