"""Live agent observer: roster, Spark queue health, and status merge logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import get_settings
from .enums import AgentStatus

# Stable actor keys from the architecture brief.
KNOWN_AGENT_ROSTER: dict[str, str] = {
    "lead_intake": "Lead Intake",
    "lead_scoring": "Lead Scoring",
    "research": "Research",
    "outbound_hunter": "Outbound Hunter",
    "outreach_writer": "Outreach Writer",
    "nurture": "Nurture",
    "crm_manager": "CRM Manager",
    "analytics": "Analytics",
    "brand_router": "Brand Router",
    "orchestrator": "Orchestrator",
}

AGENT_IDENTITY_HEADER = "X-CRM-Agent"

_STATUS_RANK: dict[AgentStatus, int] = {
    AgentStatus.IDLE: 0,
    AgentStatus.THINKING: 1,
    AgentStatus.WORKING: 2,
    AgentStatus.BLOCKED: 3,
}


@dataclass(frozen=True)
class HeartbeatSnapshot:
    agent_name: str
    status: AgentStatus
    task: str | None
    resource: str | None
    last_seen_at: datetime


@dataclass(frozen=True)
class AgentObserverRow:
    name: str
    display_name: str
    status: AgentStatus
    task: str | None
    resource: str | None
    last_heartbeat: datetime | None


def spark_queue_health_url() -> str:
    """Derive the Spark queue ``/health`` URL from ``CRM_LLM_BASE_URL``."""
    base = get_settings().llm_base_url.rstrip("/")
    base = base.removesuffix("/v1")
    return f"{base}/health"


def fetch_spark_queue_health(timeout: float = 2.0) -> dict[str, Any] | None:
    """Return Spark queue health JSON or ``None`` when unreachable."""
    try:
        response = httpx.get(spark_queue_health_url(), timeout=timeout)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        return None


def map_queue_actor_status(
    actor: str,
    *,
    waiters: list[str],
    in_flight: list[str],
) -> AgentStatus | None:
    """Map a queue actor to blocked/working, or ``None`` when not in the queue."""
    if actor in waiters:
        return AgentStatus.BLOCKED
    if actor in in_flight:
        return AgentStatus.WORKING
    return None


def merge_agent_status(
  heartbeat: HeartbeatSnapshot | None,
  queue_status: AgentStatus | None,
) -> AgentStatus:
    """Pick the highest-priority status between heartbeat and queue occupancy."""
    candidates: list[AgentStatus] = []
    if heartbeat is not None:
        candidates.append(heartbeat.status)
    if queue_status is not None:
        candidates.append(queue_status)
    if not candidates:
        return AgentStatus.IDLE
    return max(candidates, key=lambda status: _STATUS_RANK[status])


def _queue_actor_lists(
    queue_health: dict[str, Any] | None,
) -> tuple[list[str], list[str]]:
    if not queue_health:
        return [], []
    waiters = [entry.get("actor", "unknown") for entry in queue_health.get("waiters", [])]
    in_flight = [entry.get("actor", "unknown") for entry in queue_health.get("in_flight", [])]
    return waiters, in_flight


def build_observer_rows(
    heartbeats: list[HeartbeatSnapshot],
    queue_health: dict[str, Any] | None,
) -> list[AgentObserverRow]:
    """Merge roster, heartbeats, and Spark queue occupancy into observer rows."""
    heartbeat_by_name = {hb.agent_name: hb for hb in heartbeats}
    waiters, in_flight = _queue_actor_lists(queue_health)
    model = (queue_health or {}).get("model")

    rows: list[AgentObserverRow] = []
    for agent_name, display_name in KNOWN_AGENT_ROSTER.items():
        heartbeat = heartbeat_by_name.get(agent_name)
        queue_status = map_queue_actor_status(
            agent_name,
            waiters=waiters,
            in_flight=in_flight,
        )
        status = merge_agent_status(heartbeat, queue_status)

        task = heartbeat.task if heartbeat else None
        resource = heartbeat.resource if heartbeat else None
        if queue_status == AgentStatus.BLOCKED:
            resource = _spark_queue_resource(model, slot="waiting")
        elif queue_status == AgentStatus.WORKING:
            resource = _spark_queue_resource(model, slot="in-flight")

        rows.append(
            AgentObserverRow(
                name=agent_name,
                display_name=display_name,
                status=status,
                task=task,
                resource=resource,
                last_heartbeat=heartbeat.last_seen_at if heartbeat else None,
            )
        )

    # Unknown queue actors (not in roster) still appear so the observer stays honest.
    known = set(KNOWN_AGENT_ROSTER)
    extra_actors = {actor for actor in waiters + in_flight if actor not in known}
    for actor in sorted(extra_actors):
        queue_status = map_queue_actor_status(actor, waiters=waiters, in_flight=in_flight)
        rows.append(
            AgentObserverRow(
                name=actor,
                display_name=actor,
                status=queue_status or AgentStatus.IDLE,
                task=None,
                resource=_spark_queue_resource(model, slot="waiting" if actor in waiters else "in-flight"),
                last_heartbeat=None,
            )
        )

    return rows


def external_upstream_slots(queue_health: dict[str, Any] | None) -> int:
    """Hermes / other ranch agents occupying Spark without a CRM identity."""
    if not queue_health:
        return 0
    observed = int(queue_health.get("observed_upstream_in_flight", 0))
    local = int(queue_health.get("local_in_flight", 0))
    return max(0, observed - local)


def spark_slot_summary(queue_health: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize Spark queue health for the dashboard resource strip."""
    if not queue_health:
        return {
            "max_concurrency": 4,
            "observed_upstream_in_flight": 0,
            "local_in_flight": 0,
            "waiting": 0,
            "external_upstream_slots": 0,
            "model": None,
            "waiters": [],
            "in_flight": [],
        }
    waiters, in_flight = _queue_actor_lists(queue_health)
    return {
        "max_concurrency": int(queue_health.get("max_concurrency", 4)),
        "observed_upstream_in_flight": int(queue_health.get("observed_upstream_in_flight", 0)),
        "local_in_flight": int(queue_health.get("local_in_flight", 0)),
        "waiting": int(queue_health.get("waiting", 0)),
        "external_upstream_slots": external_upstream_slots(queue_health),
        "model": queue_health.get("model"),
        "waiters": waiters,
        "in_flight": in_flight,
    }


def _spark_queue_resource(model: str | None, *, slot: str) -> str:
    model_label = model or "spark"
    return f"Spark queue ({slot}, {model_label})"


def utcnow() -> datetime:
    return datetime.now(UTC)
