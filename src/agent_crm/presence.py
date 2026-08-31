"""Live agent observer: roster, Spark queue health, and status merge logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import get_settings
from .enums import AgentStatus
from .token_usage_store import load_token_usage_snapshot, merge_usage_snapshots

# Stable actor keys from the architecture brief.
KNOWN_AGENT_ROSTER: dict[str, str] = {
    "lead_intake": "Lead Intake",
    "lead_scoring": "Lead Scoring",
    "research": "Research",
    "outbound_hunter": "Outbound Hunter",
    "engagement": "Agent Engagement",
    "seo": "SEO Documents",
    "aeo-geo": "AEO / GEO Documents",
    "queue-review": "Queue Review",
    "outreach_writer": "Outreach Writer",
    "nurture": "Nurture",
    "crm_manager": "CRM Manager",
    "analytics": "Analytics",
    "brand_router": "Brand Router",
    "lead_verifier": "Lead Verifier",
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
    prompt_tokens: int = 0
    completion_tokens: int = 0
    saved_usd: float = 0.0
    tokens_per_hour: float = 0.0


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


_LOAD_PERSISTED = object()


def build_observer_rows(
    heartbeats: list[HeartbeatSnapshot],
    queue_health: dict[str, Any] | None,
    persisted_usage: dict[str, Any] | None | object = _LOAD_PERSISTED,
) -> list[AgentObserverRow]:
    """Merge roster, heartbeats, Spark queue occupancy, and token totals."""
    heartbeat_by_name = {hb.agent_name: hb for hb in heartbeats}
    waiters, in_flight = _queue_actor_lists(queue_health)
    model = (queue_health or {}).get("model")
    usage = _token_usage_block(queue_health, persisted_usage=persisted_usage)

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

        prompt_tokens, completion_tokens, saved_usd, hourly = _actor_token_fields(
            agent_name, usage
        )
        rows.append(
            AgentObserverRow(
                name=agent_name,
                display_name=display_name,
                status=status,
                task=task,
                resource=resource,
                last_heartbeat=heartbeat.last_seen_at if heartbeat else None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                saved_usd=saved_usd,
                tokens_per_hour=hourly,
            )
        )

    # Unknown queue actors (not in roster) still appear so the observer stays honest.
    # Token-only actors (e.g. Hermes after the slot frees) stay visible too.
    known = set(KNOWN_AGENT_ROSTER)
    extra_actors = {actor for actor in waiters + in_flight if actor not in known}
    extra_actors |= {
        actor for actor in (usage.get("by_actor") or {}) if actor not in known
    }
    for actor in sorted(extra_actors):
        queue_status = map_queue_actor_status(actor, waiters=waiters, in_flight=in_flight)
        if queue_status == AgentStatus.BLOCKED:
            resource = _spark_queue_resource(model, slot="waiting")
        elif queue_status == AgentStatus.WORKING:
            resource = _spark_queue_resource(model, slot="in-flight")
        else:
            resource = None
        prompt_tokens, completion_tokens, saved_usd, hourly = _actor_token_fields(
            actor, usage
        )
        rows.append(
            AgentObserverRow(
                name=actor,
                display_name=actor,
                status=queue_status or AgentStatus.IDLE,
                task=None,
                resource=resource,
                last_heartbeat=None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                saved_usd=saved_usd,
                tokens_per_hour=hourly,
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


def spark_slot_summary(
    queue_health: dict[str, Any] | None,
    persisted_usage: dict[str, Any] | None | object = None,
) -> dict[str, Any]:
    """Normalize Spark queue health for the dashboard resource strip.

    Occupancy callers pass nothing (or ``{}``) so this stays a cheap health
    parse. Pass a token snapshot when the Live Agents totals strip needs it.
    """
    usage_arg: dict[str, Any] | object = (
        {} if persisted_usage is None else persisted_usage
    )
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
            "token_usage": _token_usage_block(None, persisted_usage=usage_arg),
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
        "token_usage": _token_usage_block(queue_health, persisted_usage=usage_arg),
    }


def _spark_queue_resource(model: str | None, *, slot: str) -> str:
    model_label = model or "spark"
    return f"Spark queue ({slot}, {model_label})"


def utcnow() -> datetime:
    return datetime.now(UTC)


def avoided_cloud_usd(
    prompt_tokens: int,
    completion_tokens: int,
    *,
    input_usd_per_million: float | None = None,
    output_usd_per_million: float | None = None,
) -> float:
    """Estimate cloud API spend avoided by running the same tokens locally."""
    settings = get_settings()
    input_rate = (
        settings.llm_input_usd_per_million
        if input_usd_per_million is None
        else input_usd_per_million
    )
    output_rate = (
        settings.llm_output_usd_per_million
        if output_usd_per_million is None
        else output_usd_per_million
    )
    return round(
        (max(0, prompt_tokens) / 1_000_000.0) * input_rate
        + (max(0, completion_tokens) / 1_000_000.0) * output_rate,
        6,
    )


def _token_usage_block(
    queue_health: dict[str, Any] | None,
    persisted_usage: dict[str, Any] | None | object = _LOAD_PERSISTED,
) -> dict[str, Any]:
    raw = (queue_health or {}).get("token_usage") if queue_health else None
    if not isinstance(raw, dict):
        raw = {}
    stored = (
        load_token_usage_snapshot()
        if persisted_usage is _LOAD_PERSISTED
        else (persisted_usage if isinstance(persisted_usage, dict) else {})
    )
    merged = merge_usage_snapshots(stored, raw)
    prompt = int((merged.get("totals") or {}).get("prompt_tokens") or 0)
    completion = int((merged.get("totals") or {}).get("completion_tokens") or 0)
    settings = get_settings()
    totals = dict(merged.get("totals") or {})
    totals["saved_usd"] = avoided_cloud_usd(prompt, completion)
    return {
        "by_actor": merged.get("by_actor") or {},
        "totals": totals,
        "input_usd_per_million": settings.llm_input_usd_per_million,
        "output_usd_per_million": settings.llm_output_usd_per_million,
    }


def _actor_token_fields(
    actor: str, usage: dict[str, Any]
) -> tuple[int, int, float, float]:
    row = (usage.get("by_actor") or {}).get(actor) or {}
    prompt = int(row.get("prompt_tokens") or 0)
    completion = int(row.get("completion_tokens") or 0)
    hourly = float(row.get("tokens_per_hour") or 0.0)
    return prompt, completion, avoided_cloud_usd(prompt, completion), hourly
