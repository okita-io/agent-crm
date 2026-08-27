"""Dispatcher loop for agent_jobs — respects spark-queue global concurrency."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .config import get_settings
from .contact_people_enrichment import enrich_contact_person
from .contact_quality import is_role_inbox_email
from .contact_store import _persist_enrichment
from .db import session_scope
from .enums import AgentJobKind, AgentJobStatus, AgentStatus
from .heartbeat import record_heartbeat
from .idle_backlog import seed_idle_backlog_jobs
from .job_store import (
    claim_non_spark_jobs,
    claim_spark_jobs,
    count_pending_jobs,
    job_status_breakdown,
    mark_job_completed,
    mark_job_failed,
    reset_stale_running_jobs,
)
from .models import ContactProfile
from .orchestrator import note_job_failure
from .verifier import verify_lead

logger = logging.getLogger(__name__)

ACTOR = "job-dispatcher"


@dataclass
class JobDispatcherCycle:
    jobs_claimed: int = 0
    jobs_completed: int = 0
    jobs_failed: int = 0
    errors: list[str] = field(default_factory=list)


def execute_job(job_id: int, kind: AgentJobKind, payload: dict | None) -> None:
    """Run one claimed job."""
    payload = payload or {}
    if kind == AgentJobKind.ENRICH_CONTACT:
        profile_id = payload.get("contact_profile_id")
        if not isinstance(profile_id, int):
            raise ValueError("enrich_contact job missing contact_profile_id")
        with session_scope() as session:
            row = session.get(ContactProfile, profile_id)
            if row is None:
                raise ValueError(f"contact profile {profile_id} not found")
            if is_role_inbox_email(row.email):
                row.enrichment = {"skipped": "role_inbox"}
                return
            email = row.email
            name = row.name
        result = enrich_contact_person(
            email=email,
            name=name,
            allow_spark=True,
        )
        if result is None:
            with session_scope() as session:
                row = session.get(ContactProfile, profile_id)
                if row is not None and row.enrichment is None:
                    row.enrichment = {"skipped": "no_public_evidence"}
            return
        _persist_enrichment(email=email, result=result)
        return

    if kind == AgentJobKind.VERIFY_LEAD:
        lead_id = payload.get("lead_id")
        if not isinstance(lead_id, int):
            raise ValueError("verify_lead job missing lead_id")
        verify_lead(lead_id)
        return

    if kind == AgentJobKind.DECODE_EMAIL:
        raise NotImplementedError("decode_email jobs are not executed yet")

    raise ValueError(f"unknown job kind: {kind}")


def run_dispatcher_cycle(
    *,
    batch_size: int = 20,
    actor: str = ACTOR,
) -> JobDispatcherCycle:
    """Claim and execute pending jobs — non-Spark work drains before Spark jobs."""
    reset_stale_running_jobs()
    cycle = JobDispatcherCycle()

    non_spark_jobs = claim_non_spark_jobs(max_claim=batch_size, actor=actor)
    spark_slots = max(batch_size - len(non_spark_jobs), 0)
    spark_jobs = claim_spark_jobs(max_claim=spark_slots, actor=actor) if spark_slots else []
    jobs = non_spark_jobs + spark_jobs
    cycle.jobs_claimed = len(jobs)

    for job in jobs:
        record_heartbeat(
            actor,
            status=AgentStatus.WORKING,
            task=f"{job.kind.value} job {job.id}",
        )
        try:
            execute_job(job.id, job.kind, job.payload)
            mark_job_completed(job.id)
            cycle.jobs_completed += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception("Job %s failed", job.id)
            error_text = str(exc)
            mark_job_failed(job.id, error_text)
            note_job_failure(kind=job.kind, error_text=error_text, job_id=job.id)
            cycle.jobs_failed += 1
            cycle.errors.append(f"job {job.id}: {exc}")

    record_heartbeat(
        actor,
        status=AgentStatus.IDLE,
        task=f"cycle done ({cycle.jobs_completed} ok, {cycle.jobs_failed} failed)",
    )
    return cycle


def run_job_dispatcher(
    *,
    batch_size: int | None = None,
    poll_seconds: int | None = None,
) -> None:
    """Run forever: drain pending jobs in batches, then poll when idle."""
    settings = get_settings()
    batch = batch_size if batch_size is not None else settings.job_dispatcher_batch_size
    poll = poll_seconds if poll_seconds is not None else settings.job_dispatcher_poll_seconds

    record_heartbeat(ACTOR, status=AgentStatus.IDLE, task="job dispatcher starting")
    seed_idle_backlog_jobs(limit=settings.job_dispatcher_idle_verify_limit)
    while True:
        work_done = False
        while count_pending_jobs() > 0:
            cycle = run_dispatcher_cycle(batch_size=batch)
            if cycle.jobs_claimed == 0:
                break
            work_done = True
        if work_done:
            continue
        seeded = seed_idle_backlog_jobs(limit=settings.job_dispatcher_idle_verify_limit)
        total_seeded = seeded["verify"] + seeded["enrich"]
        if total_seeded > 0:
            continue
        idle_task = f"idle ({count_pending_jobs()} pending)"
        record_heartbeat(
            ACTOR,
            status=AgentStatus.IDLE,
            task=idle_task,
        )
        time.sleep(poll)


def build_job_status() -> dict:
    """Aggregate queue counts for API and dashboard."""
    breakdown = job_status_breakdown()
    pending_by_kind: dict[str, int] = {}
    running_by_kind: dict[str, int] = {}
    for row in breakdown:
        kind = row["kind"]
        count = row["count"]
        if row["status"] == AgentJobStatus.PENDING.value:
            pending_by_kind[kind] = pending_by_kind.get(kind, 0) + count
        elif row["status"] == AgentJobStatus.RUNNING.value:
            running_by_kind[kind] = running_by_kind.get(kind, 0) + count
    return {
        "pending_total": sum(pending_by_kind.values()),
        "running_total": sum(running_by_kind.values()),
        "pending_by_kind": pending_by_kind,
        "running_by_kind": running_by_kind,
        "breakdown": breakdown,
    }
