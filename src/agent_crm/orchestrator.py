"""Orchestrator: inspect stack health and record self-learning improvement notes."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from .config import get_settings
from .contact_quality import is_placeholder_email, is_role_inbox_email
from .db import session_scope
from .idle_backlog import seed_idle_backlog_jobs
from .enums import (
    AgentJobKind,
    AgentJobStatus,
    AgentStatus,
    ContactKind,
    ImprovementNoteKind,
    ImprovementNoteSeverity,
    ImprovementSourceAgent,
)
from .heartbeat import list_heartbeats
from .improvement_store import make_fingerprint, record_improvement_note
from .job_store import count_pending_jobs, count_running_jobs
from .models import AgentJob, ContactVerification, Lead
from .presence import fetch_spark_queue_health, spark_slot_summary

logger = logging.getLogger(__name__)

ACTOR = "orchestrator"

STANDING_WORKERS: tuple[str, ...] = (
    "job-dispatcher",
    "outbound_hunter",
    "research",
    "orchestrator",
)

STALE_HEARTBEAT_MINUTES = 10


def _note_from_failure_text(
    *,
    source_agent: ImprovementSourceAgent,
    error_text: str,
    context: str,
) -> None:
    """Record distinctive failure patterns shared by workers."""
    lowered = error_text.lower()
    if "invalidtextrepresentation" in lowered and "activitytype" in lowered:
        record_improvement_note(
            kind=ImprovementNoteKind.REPAIR,
            severity=ImprovementNoteSeverity.CRITICAL,
            source_agent=source_agent,
            title="Postgres activitytype missing VERIFIED",
            body=(
                f"{context}: verify_lead activity logging failed because Postgres "
                "activitytype enum is missing VERIFIED."
            ),
            metrics={"error": error_text[:500]},
            suggested_fix=(
                "Run alembic upgrade head (revision i4j5k6l7m8n9 adds VERIFIED)."
            ),
            fingerprint=make_fingerprint("repair", "activitytype", "VERIFIED"),
        )
    if "enum" in lowered and "invalid" in lowered:
        record_improvement_note(
            kind=ImprovementNoteKind.REPAIR,
            severity=ImprovementNoteSeverity.CRITICAL,
            source_agent=source_agent,
            title="Postgres enum mismatch",
            body=f"{context}: {error_text[:800]}",
            metrics={"error": error_text[:500]},
            suggested_fix="Add missing enum values via Alembic ALTER TYPE ADD VALUE.",
            fingerprint=make_fingerprint("repair", "enum", error_text[:120]),
        )
    if " 500 " in f" {error_text} " or "internal server error" in lowered:
        record_improvement_note(
            kind=ImprovementNoteKind.PERFORMANCE,
            severity=ImprovementNoteSeverity.WARN,
            source_agent=source_agent,
            title="HTTP 500 burst detected",
            body=f"{context}: upstream returned HTTP 500.",
            metrics={"error": error_text[:500]},
            suggested_fix="Check spark-queue health and Spark SGLang logs.",
            fingerprint=make_fingerprint("performance", "http500", source_agent.value),
        )


def note_worker_failure(
    *,
    source_agent: ImprovementSourceAgent,
    error_text: str,
    context: str,
) -> None:
    """Record distinctive worker failures for orchestrator follow-up."""
    _note_from_failure_text(
        source_agent=source_agent,
        error_text=error_text,
        context=context,
    )


def note_job_failure(*, kind: AgentJobKind, error_text: str, job_id: int) -> None:
    """Append an improvement note for a failed background job."""
    _note_from_failure_text(
        source_agent=ImprovementSourceAgent.JOB_DISPATCHER,
        error_text=error_text,
        context=f"{kind.value} job {job_id} failed",
    )


def _check_stale_heartbeats(now: datetime) -> None:
    cutoff = now - timedelta(minutes=STALE_HEARTBEAT_MINUTES)
    heartbeats = {row.agent_name: row for row in list_heartbeats()}
    for agent_name in STANDING_WORKERS:
        snapshot = heartbeats.get(agent_name)
        if snapshot is None or snapshot.last_seen_at < cutoff:
            record_improvement_note(
                kind=ImprovementNoteKind.GAP,
                severity=ImprovementNoteSeverity.WARN,
                source_agent=ImprovementSourceAgent.ORCHESTRATOR,
                title=f"{agent_name} heartbeat stale",
                body=(
                    f"No fresh heartbeat from {agent_name} in the last "
                    f"{STALE_HEARTBEAT_MINUTES} minutes."
                ),
                metrics={
                    "agent": agent_name,
                    "last_seen_at": (
                        snapshot.last_seen_at.isoformat() if snapshot else None
                    ),
                },
                suggested_fix=(
                    f"Ensure docker compose service for {agent_name} is running "
                    "and recording heartbeats."
                ),
                fingerprint=make_fingerprint("gap", "stale-heartbeat", agent_name),
            )


def _check_failed_jobs() -> None:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(AgentJob)
                .where(AgentJob.status == AgentJobStatus.FAILED)
                .order_by(AgentJob.completed_at.desc())
                .limit(20)
            )
        )
    for row in rows:
        if not row.error_message:
            continue
        _note_from_failure_text(
            source_agent=ImprovementSourceAgent.JOB_DISPATCHER,
            error_text=row.error_message,
            context=f"failed {row.kind.value} job {row.id}",
        )


def _check_spark_queue() -> None:
    health = fetch_spark_queue_health()
    if health is None:
        record_improvement_note(
            kind=ImprovementNoteKind.GAP,
            severity=ImprovementNoteSeverity.WARN,
            source_agent=ImprovementSourceAgent.SPARK_QUEUE,
            title="spark-queue health unreachable",
            body="Orchestrator could not reach spark-queue /health.",
            metrics={},
            suggested_fix="Check spark-queue container and CRM_LLM_BASE_URL.",
            fingerprint=make_fingerprint("gap", "spark-queue", "unreachable"),
        )
        return

    summary = spark_slot_summary(health)
    if int(summary.get("observed_upstream_in_flight", 0)) >= int(
        summary.get("max_concurrency", 4)
    ):
        record_improvement_note(
            kind=ImprovementNoteKind.PERFORMANCE,
            severity=ImprovementNoteSeverity.INFO,
            source_agent=ImprovementSourceAgent.SPARK_QUEUE,
            title="Spark queue at capacity",
            body="All Spark slots are in use.",
            metrics=summary,
            suggested_fix="Verify jobs should still drain; enrich waits on spark-queue.",
            fingerprint=make_fingerprint("performance", "spark-queue", "at-capacity"),
        )


def _check_verify_backlog() -> None:
    pending_verify = count_pending_jobs(kind=AgentJobKind.VERIFY_LEAD)
    pending_enrich = count_pending_jobs(kind=AgentJobKind.ENRICH_CONTACT)
    spark_running = count_running_jobs(spark_only=True)
    if pending_verify <= 0:
        return
    if spark_running >= 4 and pending_verify > pending_enrich:
        record_improvement_note(
            kind=ImprovementNoteKind.PERFORMANCE,
            severity=ImprovementNoteSeverity.WARN,
            source_agent=ImprovementSourceAgent.JOB_DISPATCHER,
            title="verify_lead backlog while Spark saturated",
            body=(
                f"{pending_verify} verify_lead jobs pending with {spark_running} "
                "Spark jobs running."
            ),
            metrics={
                "pending_verify": pending_verify,
                "pending_enrich": pending_enrich,
                "spark_running": spark_running,
            },
            suggested_fix=(
                "Non-Spark verify jobs must execute before Spark enrich in each cycle."
            ),
            fingerprint=make_fingerprint(
                "performance", "verify-backlog", "spark-saturated"
            ),
        )


def _check_verification_coverage() -> None:
    with session_scope() as session:
        leads_with_email = int(
            session.scalar(
                select(func.count())
                .select_from(Lead)
                .where(Lead.email.is_not(None))
                .where(func.length(func.trim(Lead.email)) > 0)
            )
            or 0
        )
        verified_lead_ids = select(ContactVerification.lead_id).distinct()
        unverified = int(
            session.scalar(
                select(func.count())
                .select_from(Lead)
                .where(Lead.email.is_not(None))
                .where(func.length(func.trim(Lead.email)) > 0)
                .where(Lead.id.not_in(verified_lead_ids))
            )
            or 0
        )
    if leads_with_email == 0:
        return
    ratio = unverified / leads_with_email
    if ratio >= 0.5 and unverified >= 10:
        record_improvement_note(
            kind=ImprovementNoteKind.GAP,
            severity=ImprovementNoteSeverity.WARN,
            source_agent=ImprovementSourceAgent.ORCHESTRATOR,
            title="Low verification coverage",
            body=(
                f"{unverified} of {leads_with_email} email leads lack "
                "contact_verifications rows."
            ),
            metrics={
                "unverified": unverified,
                "leads_with_email": leads_with_email,
                "ratio": round(ratio, 3),
            },
            suggested_fix=(
                "Ensure contact-worker seeds verify_lead on upsert and when idle."
            ),
            fingerprint=make_fingerprint("gap", "verification-coverage"),
        )


def _check_dummy_email_slips() -> None:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(ContactVerification)
                .where(ContactVerification.contact_kind == ContactKind.EMAIL)
                .order_by(ContactVerification.checked_at.desc())
                .limit(200)
            )
        )
    for row in rows:
        email = row.contact.strip().lower()
        if row.status.value != "valid":
            continue
        if not is_placeholder_email(email) and not is_role_inbox_email(email):
            continue
        record_improvement_note(
            kind=ImprovementNoteKind.GAP,
            severity=ImprovementNoteSeverity.WARN,
            source_agent=ImprovementSourceAgent.LEAD_VERIFIER,
            title="Dummy or role inbox marked valid",
            body=f"Verifier marked {email} as valid despite quality filters.",
            metrics={"contact": email, "lead_id": row.lead_id},
            suggested_fix="Tighten verifier quality gate for CONTACT leads.",
            fingerprint=make_fingerprint("gap", "dummy-valid", email),
        )


def run_orchestrator_cycle() -> None:
    """Run one orchestration inspection pass."""
    from .hunt_store import HuntStore
    from .job_store import reset_stale_running_jobs

    settings = get_settings()
    now = datetime.now(UTC)
    reset_stale_running_jobs()
    HuntStore().reset_stale_running_queries()
    _check_stale_heartbeats(now)
    _check_failed_jobs()
    _check_spark_queue()
    _check_verify_backlog()
    _check_verification_coverage()
    _check_dummy_email_slips()
    seed_idle_backlog_jobs(limit=settings.job_dispatcher_idle_verify_limit)


def run_orchestrator(*, poll_seconds: int | None = None) -> None:
    """Run forever: inspect stack health and record improvement notes."""
    from .heartbeat import record_heartbeat
    from .idle_backlog import seed_idle_backlog_jobs

    settings = get_settings()
    poll = poll_seconds if poll_seconds is not None else settings.orchestrator_poll_seconds

    record_heartbeat(ACTOR, status=AgentStatus.IDLE, task="orchestrator starting")
    seed_idle_backlog_jobs(limit=settings.job_dispatcher_idle_verify_limit)
    while True:
        record_heartbeat(
            ACTOR,
            status=AgentStatus.THINKING,
            task="inspecting stack health",
        )
        try:
            run_orchestrator_cycle()
        except Exception:  # noqa: BLE001
            logger.exception("Orchestrator cycle failed")
            record_improvement_note(
                kind=ImprovementNoteKind.REPAIR,
                severity=ImprovementNoteSeverity.CRITICAL,
                source_agent=ImprovementSourceAgent.ORCHESTRATOR,
                title="Orchestrator cycle crashed",
                body="Orchestrator inspection loop raised an exception.",
                suggested_fix="Inspect orchestrator logs and database connectivity.",
                fingerprint=make_fingerprint("repair", "orchestrator", "cycle-crash"),
            )
        record_heartbeat(
            ACTOR,
            status=AgentStatus.IDLE,
            task="orchestration cycle complete",
        )
        time.sleep(poll)
