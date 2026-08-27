"""Postgres-backed job queue for enrichment, verification, and Spark decode."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from .config import get_settings
from .db import session_scope, with_row_lock
from .enums import AgentJobKind, AgentJobStatus, Brand, SPARK_AGENT_JOB_KINDS
from .models import AgentJob
from .presence import fetch_spark_queue_health, spark_slot_summary

logger = logging.getLogger(__name__)

DEFAULT_ENRICH_PRIORITY = 80
DEFAULT_VERIFY_PRIORITY = 60
DEFAULT_DECODE_PRIORITY = 70
DEFAULT_QUALIFY_PRIORITY = 75
DEFAULT_TOPICAL_PRIORITY = 70


def _utcnow() -> datetime:
    return datetime.now(UTC)


def spark_queue_has_capacity() -> bool:
    """Return True when spark-queue reports fewer than max_concurrency in-flight slots."""
    settings = get_settings()
    max_slots = 4
    observed = 0
    health = fetch_spark_queue_health()
    if health is not None:
        summary = spark_slot_summary(health)
        max_slots = int(summary.get("max_concurrency", 4))
        observed = int(summary.get("observed_upstream_in_flight", 0))
    running_jobs = count_running_jobs(spark_only=True)
    effective = max(observed, running_jobs)
    return effective < max_slots


def count_running_jobs(*, spark_only: bool = False) -> int:
    with session_scope() as session:
        stmt = (
            select(func.count())
            .select_from(AgentJob)
            .where(AgentJob.status == AgentJobStatus.RUNNING)
        )
        if spark_only:
            stmt = stmt.where(AgentJob.kind.in_(tuple(SPARK_AGENT_JOB_KINDS)))
        return int(session.scalar(stmt) or 0)


def count_pending_jobs(*, kind: AgentJobKind | None = None) -> int:
    with session_scope() as session:
        stmt = (
            select(func.count())
            .select_from(AgentJob)
            .where(AgentJob.status == AgentJobStatus.PENDING)
        )
        if kind is not None:
            stmt = stmt.where(AgentJob.kind == kind)
        return int(session.scalar(stmt) or 0)


def job_status_breakdown() -> list[dict[str, Any]]:
    """Return pending/running counts grouped by job kind."""
    with session_scope() as session:
        stmt = (
            select(AgentJob.kind, AgentJob.status, func.count())
            .where(
                AgentJob.status.in_(
                    [AgentJobStatus.PENDING, AgentJobStatus.RUNNING]
                )
            )
            .group_by(AgentJob.kind, AgentJob.status)
            .order_by(AgentJob.kind.asc(), AgentJob.status.asc())
        )
        return [
            {
                "kind": kind.value,
                "status": status.value,
                "count": count,
            }
            for kind, status, count in session.execute(stmt)
        ]


def enqueue_job(
    *,
    kind: AgentJobKind,
    dedupe_key: str,
    payload: dict[str, Any],
    priority: int = 50,
) -> bool:
    """Enqueue a job if no active or completed row exists for ``dedupe_key``."""
    with session_scope() as session:
        existing = session.scalar(
            select(AgentJob).where(AgentJob.dedupe_key == dedupe_key).limit(1)
        )
        if existing is not None:
            if existing.status in (
                AgentJobStatus.PENDING,
                AgentJobStatus.RUNNING,
                AgentJobStatus.COMPLETED,
            ):
                return False
            if existing.status == AgentJobStatus.FAILED:
                existing.status = AgentJobStatus.PENDING
                existing.error_message = None
                existing.actor = None
                existing.claimed_at = None
                existing.completed_at = None
                existing.payload = payload
                existing.priority = priority
                return True
        session.add(
            AgentJob(
                kind=kind,
                status=AgentJobStatus.PENDING,
                payload=payload,
                priority=priority,
                dedupe_key=dedupe_key,
            )
        )
        return True


def enqueue_enrich_contact_job(contact_profile_id: int, *, priority: int | None = None) -> bool:
    return enqueue_job(
        kind=AgentJobKind.ENRICH_CONTACT,
        dedupe_key=f"enrich_contact:{contact_profile_id}",
        payload={"contact_profile_id": contact_profile_id},
        priority=priority or DEFAULT_ENRICH_PRIORITY,
    )


def enqueue_verify_lead_job(lead_id: int, *, priority: int | None = None) -> bool:
    return enqueue_job(
        kind=AgentJobKind.VERIFY_LEAD,
        dedupe_key=f"verify_lead:{lead_id}",
        payload={"lead_id": lead_id},
        priority=priority or DEFAULT_VERIFY_PRIORITY,
    )


def enqueue_decode_email_job(
    *,
    source_url: str,
    obfuscation_span: str,
    priority: int | None = None,
) -> bool:
    key = f"decode_email:{source_url}:{hash(obfuscation_span) & 0xFFFFFFFF:08x}"
    return enqueue_job(
        kind=AgentJobKind.DECODE_EMAIL,
        dedupe_key=key[:512],
        payload={"source_url": source_url, "span": obfuscation_span[:2000]},
        priority=priority or DEFAULT_DECODE_PRIORITY,
    )


def enqueue_qualify_contact_job(
    *,
    contact_profile_id: int | None = None,
    comment_person_id: int | None = None,
    priority: int | None = None,
) -> bool:
    if contact_profile_id is not None and comment_person_id is not None:
        raise ValueError("specify contact_profile_id or comment_person_id, not both")
    if contact_profile_id is not None:
        dedupe_key = f"qualify_contact:profile:{contact_profile_id}"
        payload: dict[str, Any] = {"contact_profile_id": contact_profile_id}
    elif comment_person_id is not None:
        dedupe_key = f"qualify_contact:comment:{comment_person_id}"
        payload = {"comment_person_id": comment_person_id}
    else:
        raise ValueError("contact_profile_id or comment_person_id required")
    return enqueue_job(
        kind=AgentJobKind.QUALIFY_CONTACT,
        dedupe_key=dedupe_key,
        payload=payload,
        priority=priority or DEFAULT_QUALIFY_PRIORITY,
    )


def enqueue_topical_relevance_job(
    *,
    url: str,
    brand: Brand,
    source_kind: str | None = None,
    source_id: int | None = None,
    query: str | None = None,
    priority: int | None = None,
) -> bool:
    from .enums import Brand as BrandEnum
    from .topic_relevance_store import normalize_url

    if not isinstance(brand, BrandEnum):
        brand = BrandEnum(brand)
    normalized = normalize_url(url)
    dedupe_key = f"check_topical:{brand.value}:{normalized}"[:512]
    payload: dict[str, Any] = {"url": normalized, "brand": brand.value}
    if source_kind:
        payload["source_kind"] = source_kind
    if source_id is not None:
        payload["source_id"] = source_id
    if query:
        payload["query"] = query
    return enqueue_job(
        kind=AgentJobKind.CHECK_TOPICAL_RELEVANCE,
        dedupe_key=dedupe_key,
        payload=payload,
        priority=priority or DEFAULT_TOPICAL_PRIORITY,
    )


def count_pending_jobs_by_kind() -> dict[AgentJobKind, int]:
    """Return pending job counts keyed by kind."""
    with session_scope() as session:
        stmt = (
            select(AgentJob.kind, func.count())
            .select_from(AgentJob)
            .where(AgentJob.status == AgentJobStatus.PENDING)
            .group_by(AgentJob.kind)
        )
        return {kind: int(count) for kind, count in session.execute(stmt)}


def pending_kind_lag_metrics() -> dict[AgentJobKind, tuple[int, datetime]]:
    """Return pending count and oldest ``created_at`` per kind."""
    with session_scope() as session:
        stmt = (
            select(
                AgentJob.kind,
                func.count(),
                func.min(AgentJob.created_at),
            )
            .where(AgentJob.status == AgentJobStatus.PENDING)
            .group_by(AgentJob.kind)
        )
        metrics: dict[AgentJobKind, tuple[int, datetime]] = {}
        for kind, count, oldest in session.execute(stmt):
            metrics[kind] = (int(count), oldest or _utcnow())
        return metrics


def _kind_can_be_claimed_now(kind: AgentJobKind, spark_running: int) -> bool:
    if kind in SPARK_AGENT_JOB_KINDS:
        return spark_queue_has_capacity() and spark_running < 4
    return True


def pick_furthest_behind_kind(
    metrics: dict[AgentJobKind, tuple[int, datetime]],
    *,
    spark_running: int,
) -> AgentJobKind | None:
    """Pick the job kind with the largest pending backlog that can run now."""
    candidates: list[tuple[AgentJobKind, int, datetime]] = []
    for kind, (count, oldest) in metrics.items():
        if count <= 0:
            continue
        if not _kind_can_be_claimed_now(kind, spark_running):
            continue
        candidates.append((kind, count, oldest))
    if not candidates:
        return None
    # Largest backlog wins; tie-break on oldest pending job in that kind.
    return max(candidates, key=lambda row: (row[1], -row[2].timestamp()))[0]


def _claim_one_job_of_kind(kind: AgentJobKind, actor: str) -> AgentJob | None:
    with session_scope() as session:
        stmt = (
            select(AgentJob)
            .where(AgentJob.status == AgentJobStatus.PENDING)
            .where(AgentJob.kind == kind)
            .order_by(AgentJob.created_at.asc(), AgentJob.id.asc())
            .limit(1)
        )
        job = session.scalar(with_row_lock(stmt, session))
        if job is None:
            return None
        job.status = AgentJobStatus.RUNNING
        job.actor = actor
        job.claimed_at = _utcnow()
        session.flush()
        return job


def claim_jobs(
    *,
    max_claim: int = 20,
    actor: str = "job-dispatcher",
) -> list[AgentJob]:
    """Claim pending jobs using furthest-behind scheduling per kind."""
    non_spark = claim_non_spark_jobs(max_claim=max_claim, actor=actor)
    remaining = max(max_claim - len(non_spark), 0)
    if remaining <= 0:
        return non_spark
    spark_jobs = claim_spark_jobs(max_claim=remaining, actor=actor)
    return non_spark + spark_jobs


def claim_non_spark_jobs(
    *,
    max_claim: int = 20,
    actor: str = "job-dispatcher",
) -> list[AgentJob]:
    """Claim pending non-Spark jobs (verify_lead) without Spark capacity checks."""
    claimed: list[AgentJob] = []
    while len(claimed) < max_claim:
        job = _claim_one_non_spark_job(actor)
        if job is None:
            break
        claimed.append(job)
    return claimed


def _claim_one_non_spark_job(actor: str) -> AgentJob | None:
    with session_scope() as session:
        stmt = (
            select(AgentJob)
            .where(AgentJob.status == AgentJobStatus.PENDING)
            .where(AgentJob.kind.not_in(tuple(SPARK_AGENT_JOB_KINDS)))
            .order_by(AgentJob.created_at.asc(), AgentJob.id.asc())
            .limit(1)
        )
        job = session.scalar(with_row_lock(stmt, session))
        if job is None:
            return None
        job.status = AgentJobStatus.RUNNING
        job.actor = actor
        job.claimed_at = _utcnow()
        session.flush()
        return job


def claim_spark_jobs(
    *,
    max_claim: int = 20,
    actor: str = "job-dispatcher",
) -> list[AgentJob]:
    """Claim pending Spark jobs respecting spark-queue global concurrency."""
    claimed: list[AgentJob] = []
    spark_running = count_running_jobs(spark_only=True)
    metrics = {
        kind: values
        for kind, values in pending_kind_lag_metrics().items()
        if kind in SPARK_AGENT_JOB_KINDS
    }

    while len(claimed) < max_claim and metrics:
        lag_kind = pick_furthest_behind_kind(metrics, spark_running=spark_running)
        if lag_kind is None:
            break
        job = _claim_one_job_of_kind(lag_kind, actor)
        if job is None:
            metrics.pop(lag_kind, None)
            continue
        claimed.append(job)
        count, oldest = metrics.get(lag_kind, (0, _utcnow()))
        remaining = count - 1
        if remaining <= 0:
            metrics.pop(lag_kind, None)
        else:
            metrics[lag_kind] = (remaining, oldest)
        if job.kind in SPARK_AGENT_JOB_KINDS:
            spark_running += 1

    return claimed


def mark_job_completed(job_id: int) -> None:
    with session_scope() as session:
        row = session.get(AgentJob, job_id)
        if row is None:
            return
        row.status = AgentJobStatus.COMPLETED
        row.completed_at = _utcnow()


def mark_job_failed(job_id: int, error: str) -> None:
    with session_scope() as session:
        row = session.get(AgentJob, job_id)
        if row is None:
            return
        row.status = AgentJobStatus.FAILED
        row.completed_at = _utcnow()
        row.error_message = error[:2000]


def reset_stale_running_jobs(*, stale_minutes: int = 30) -> int:
    """Return stuck running jobs to pending (crash recovery)."""
    cutoff = _utcnow() - timedelta(minutes=stale_minutes)
    reset = 0
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(AgentJob).where(
                    AgentJob.status == AgentJobStatus.RUNNING,
                    AgentJob.claimed_at.is_not(None),
                    AgentJob.claimed_at < cutoff,
                )
            )
        )
        for row in rows:
            row.status = AgentJobStatus.PENDING
            row.actor = None
            row.claimed_at = None
            reset += 1
    return reset
