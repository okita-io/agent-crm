"""Dispatcher loop for agent_jobs — respects spark-queue global concurrency."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .agent_control import stop_if_disabled, wait_while_disabled
from .config import get_settings
from .contact_people_enrichment import enrich_contact_person
from .contact_qualification import qualify_comment_person, qualify_contact_profile
from .contact_quality import is_role_inbox_email
from .topic_relevance_store import check_topical_relevance_job
from .contact_store import ContactExtractionBudget, _persist_enrichment
from .db import session_scope
from .enums import AgentJobKind, AgentJobStatus, AgentStatus, Brand
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
from .models import ContactProfile, HuntResource
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


def execute_job(
    job_id: int,
    kind: AgentJobKind,
    payload: dict | None,
    *,
    budget: ContactExtractionBudget | None = None,
) -> None:
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
            budget=budget,
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

    if kind == AgentJobKind.QUALIFY_CONTACT:
        profile_id = payload.get("contact_profile_id")
        person_id = payload.get("comment_person_id")
        if isinstance(profile_id, int):
            qualify_contact_profile(profile_id, allow_spark=True)
            return
        if isinstance(person_id, int):
            qualify_comment_person(person_id, allow_spark=True)
            return
        raise ValueError("qualify_contact job missing contact_profile_id or comment_person_id")

    if kind == AgentJobKind.CHECK_TOPICAL_RELEVANCE:
        url = payload.get("url")
        brand_raw = payload.get("brand")
        if not isinstance(url, str) or not isinstance(brand_raw, str):
            raise ValueError("check_topical_relevance job missing url or brand")

        check_topical_relevance_job(
            url=url,
            brand=Brand(brand_raw),
            source_kind=payload.get("source_kind")
            if isinstance(payload.get("source_kind"), str)
            else None,
            source_id=payload.get("source_id")
            if isinstance(payload.get("source_id"), int)
            else None,
            query=payload.get("query") if isinstance(payload.get("query"), str) else None,
            allow_spark=True,
        )
        return

    if kind == AgentJobKind.DECODE_EMAIL:
        _execute_decode_email(payload)
        return

    raise ValueError(f"unknown job kind: {kind}")


def _execute_decode_email(payload: dict) -> None:
    """Decode an obfuscated email span via Spark and upsert contacts."""
    from sqlalchemy import select

    from .contact_extractor import decode_obfuscated_emails_spark
    from .contact_store import upsert_contact_profile

    source_url = payload.get("source_url")
    span = payload.get("span")
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError("decode_email job missing source_url")
    if not isinstance(span, str) or not span.strip():
        raise ValueError("decode_email job missing span")

    brand = Brand.UNASSIGNED
    with session_scope() as session:
        resource = session.scalar(
            select(HuntResource).where(HuntResource.url == source_url).limit(1)
        )
        if resource is not None:
            brand = resource.brand

    # Explicit decode jobs always get one Spark decode attempt.
    decoded = decode_obfuscated_emails_spark(
        span,
        budget=ContactExtractionBudget(
            social_lookups_remaining=0,
            spark_decode_remaining=1,
        ),
        max_spans=1,
    )
    for email, name in decoded:
        try:
            upsert_contact_profile(
                email=email,
                name=name,
                brand=brand,
                source_url=source_url,
            )
        except ValueError:
            continue

def run_dispatcher_cycle(
    *,
    batch_size: int = 20,
    actor: str = ACTOR,
) -> JobDispatcherCycle:
    """Claim and execute pending jobs — non-Spark work drains before Spark jobs."""
    if stop_if_disabled(actor):
        return JobDispatcherCycle()
    reset_stale_running_jobs()
    cycle = JobDispatcherCycle()
    budget = ContactExtractionBudget.from_settings()

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
            execute_job(job.id, job.kind, job.payload, budget=budget)
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
        wait_while_disabled(ACTOR)
        work_done = False
        while count_pending_jobs() > 0:
            cycle = run_dispatcher_cycle(batch_size=batch)
            if cycle.jobs_claimed == 0:
                break
            work_done = True
        if work_done:
            continue
        seeded = seed_idle_backlog_jobs(limit=settings.job_dispatcher_idle_verify_limit)
        total_seeded = (
            seeded["verify"]
            + seeded["enrich"]
            + seeded.get("qualify", 0)
            + seeded.get("topical", 0)
        )
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
