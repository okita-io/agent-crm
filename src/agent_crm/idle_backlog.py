"""Idle backlog seeding for orchestrator and contact-worker.

When the job queue is empty, standing workers still wake periodically and
enqueue work for whichever kind is furthest behind (verify vs enrich).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from .contact_quality import is_role_inbox_email
from .db import session_scope
from .enums import AgentJobKind
from .job_store import (
    count_pending_jobs_by_kind,
    count_running_jobs,
    enqueue_enrich_contact_job,
    pending_kind_lag_metrics,
    pick_furthest_behind_kind,
)
from .models import ContactProfile, ContactVerification, Lead
from .verifier import count_unverified_email_leads, seed_verify_jobs_for_unverified

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def count_unenriched_person_profiles() -> int:
    """Count person profiles that still need enrichment."""
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(ContactProfile.email)
                .where(ContactProfile.enrichment.is_(None))
                .limit(500)
            )
        )
    return sum(1 for email in rows if not is_role_inbox_email(email))


def seed_enrich_jobs_for_unenriched(*, limit: int = 50) -> int:
    """Enqueue enrich_contact jobs for profiles missing enrichment."""
    if limit <= 0:
        return 0

    with session_scope() as session:
        candidates = list(
            session.execute(
                select(ContactProfile.id, ContactProfile.email)
                .where(ContactProfile.enrichment.is_(None))
                .order_by(ContactProfile.updated_at.asc(), ContactProfile.id.asc())
                .limit(limit * 3)
            )
        )

    enqueued = 0
    for profile_id, email in candidates:
        if enqueued >= limit:
            break
        if is_role_inbox_email(email):
            continue
        if enqueue_enrich_contact_job(profile_id):
            enqueued += 1
    return enqueued


def seed_idle_backlog_jobs(*, limit: int = 50) -> dict[str, int]:
    """Seed verify or enrich jobs for the furthest-behind backlog kind."""
    pending_metrics = pending_kind_lag_metrics()
    pending_counts = count_pending_jobs_by_kind()
    metrics: dict[AgentJobKind, tuple[int, datetime]] = dict(pending_metrics)

    unverified = count_unverified_email_leads()
    if unverified > 0:
        count, oldest = metrics.get(AgentJobKind.VERIFY_LEAD, (0, _EPOCH))
        metrics[AgentJobKind.VERIFY_LEAD] = (count + unverified, oldest)

    unenriched = count_unenriched_person_profiles()
    if unenriched > 0:
        count, oldest = metrics.get(AgentJobKind.ENRICH_CONTACT, (0, _EPOCH))
        metrics[AgentJobKind.ENRICH_CONTACT] = (count + unenriched, oldest)

    spark_running = count_running_jobs(spark_only=True)
    lag_kind = pick_furthest_behind_kind(metrics, spark_running=spark_running)

    if lag_kind == AgentJobKind.ENRICH_CONTACT and unenriched > 0:
        return {
            "verify": 0,
            "enrich": seed_enrich_jobs_for_unenriched(limit=limit),
        }

    if unverified > 0:
        return {
            "verify": seed_verify_jobs_for_unverified(limit=limit),
            "enrich": 0,
        }

    if pending_counts.get(AgentJobKind.ENRICH_CONTACT, 0) > 0:
        return {"verify": 0, "enrich": 0}

    if unenriched > 0:
        return {
            "verify": 0,
            "enrich": seed_enrich_jobs_for_unenriched(limit=limit),
        }

    return {"verify": 0, "enrich": 0}
