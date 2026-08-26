"""Live hunt-loop status for the dashboard and ``GET /hunt/status``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from agent_crm.contact_store import count_contact_emails_by_brand_audience
from agent_crm.enums import Brand, ContactAudience, HuntQueryStatus
from agent_crm.hunt_seeds import audience_from_origin
from agent_crm.hunt_store import HuntStore
from agent_crm.presence import fetch_spark_queue_health, spark_slot_summary

STALE_RUNNING_MINUTES = 15
TACTIC_STUDIO_EMAIL_GOAL = 100
RECENTLY_COMPLETED_LIMIT = 8


def is_fresh_running(
    updated_at: datetime,
    *,
    now: datetime | None = None,
    stale_minutes: int = STALE_RUNNING_MINUTES,
) -> bool:
    """Return True when a ``running`` row was touched recently enough to trust."""
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    return reference - updated_at < timedelta(minutes=stale_minutes)


def infer_hunt_phase(
    *,
    has_fresh_running: bool,
    spark_waiting: int,
    spark_in_flight: int,
) -> str:
    """Infer scrape vs LLM phase from spark-queue occupancy and running queries."""
    if spark_in_flight > 0 or spark_waiting > 0:
        return "LLM / Spark"
    if has_fresh_running:
        return "Searching / scraping (GPU idle until this query finishes)"
    return "idle / queue empty"


def _serialize_running_query(row: Any, *, now: datetime | None = None) -> dict[str, Any]:
    reference = now or datetime.now(UTC)
    updated_at = row.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    audience = audience_from_origin(row.origin)
    return {
        "id": row.id,
        "query": row.query,
        "brand": row.brand.value,
        "priority": row.priority,
        "origin": row.origin,
        "audience": audience.value if audience else None,
        "updated_at": updated_at,
        "running_seconds": int((reference - updated_at).total_seconds()),
    }


def build_hunt_status(
    *,
    store: HuntStore | None = None,
    queue_health: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate hunt-loop drain status for the dashboard and API."""
    store = store or HuntStore()
    reference = now or datetime.now(UTC)
    if queue_health is None:
        queue_health = fetch_spark_queue_health()
    spark = spark_slot_summary(queue_health)

    running_row = store.current_running_query(
        stale_minutes=STALE_RUNNING_MINUTES,
        now=reference,
    )
    has_fresh_running = running_row is not None
    phase = infer_hunt_phase(
        has_fresh_running=has_fresh_running,
        spark_waiting=int(spark.get("waiting", 0)),
        spark_in_flight=len(spark.get("in_flight", [])),
    )

    aggregate = store.queue_status()
    queue_rows = store.queue_breakdown()
    email_counts = count_contact_emails_by_brand_audience()
    tactic_total = sum(
        row["count"]
        for row in email_counts
        if row["brand"] == Brand.TACTIC_STUDIO.value
    )
    recently_completed = [
        {
            "id": row.id,
            "query": row.query,
            "brand": row.brand.value,
            "updated_at": row.completed_at or row.updated_at,
        }
        for row in store.recently_completed_queries(limit=RECENTLY_COMPLETED_LIMIT)
    ]

    return {
        "phase": phase,
        "now_playing": (
            _serialize_running_query(running_row, now=reference)
            if running_row is not None
            else None
        ),
        "pending": aggregate["pending"],
        "by_status": aggregate["by_status"],
        "total_resources": aggregate["total_resources"],
        "queue_breakdown": queue_rows,
        "email_counts": email_counts,
        "tactic_studio_email_total": tactic_total,
        "tactic_studio_email_goal": TACTIC_STUDIO_EMAIL_GOAL,
        "recently_completed": recently_completed,
        "spark": {
            "waiting": int(spark.get("waiting", 0)),
            "in_flight": len(spark.get("in_flight", [])),
            "model": spark.get("model"),
        },
    }
