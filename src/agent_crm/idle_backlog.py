"""Idle backlog seeding for orchestrator and contact-worker.

When the job queue is empty, standing workers still wake periodically and
enqueue work for whichever kind is furthest behind (verify vs enrich).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from .agent_control import dispatcher_work_allowed
from .contact_qualification import count_unqualified_contacts, seed_qualify_jobs_for_unqualified
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
from .models import ContactProfile
from .topic_relevance_store import count_urls_needing_topical_check, seed_topical_relevance_jobs
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
    """Seed verify, enrich, or qualify jobs for the furthest-behind backlog kind.

    No-op when the job dispatcher is paused so work does not pile up for a
    disabled agent.
    """
    empty = {"verify": 0, "enrich": 0, "qualify": 0, "topical": 0}
    if not dispatcher_work_allowed():
        return empty

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

    unqualified = count_unqualified_contacts()
    if unqualified > 0:
        count, oldest = metrics.get(AgentJobKind.QUALIFY_CONTACT, (0, _EPOCH))
        metrics[AgentJobKind.QUALIFY_CONTACT] = (count + unqualified, oldest)

    unchecked_urls = count_urls_needing_topical_check()
    if unchecked_urls > 0:
        count, oldest = metrics.get(AgentJobKind.CHECK_TOPICAL_RELEVANCE, (0, _EPOCH))
        metrics[AgentJobKind.CHECK_TOPICAL_RELEVANCE] = (count + unchecked_urls, oldest)

    spark_running = count_running_jobs(spark_only=True)
    lag_kind = pick_furthest_behind_kind(metrics, spark_running=spark_running)

    if lag_kind == AgentJobKind.CHECK_TOPICAL_RELEVANCE and unchecked_urls > 0:
        return {
            "verify": 0,
            "enrich": 0,
            "qualify": 0,
            "topical": seed_topical_relevance_jobs(limit=limit),
        }

    if lag_kind == AgentJobKind.QUALIFY_CONTACT and unqualified > 0:
        return {
            "verify": 0,
            "enrich": 0,
            "qualify": seed_qualify_jobs_for_unqualified(limit=limit),
            "topical": 0,
        }

    if lag_kind == AgentJobKind.ENRICH_CONTACT and unenriched > 0:
        return {
            "verify": 0,
            "enrich": seed_enrich_jobs_for_unenriched(limit=limit),
            "qualify": 0,
            "topical": 0,
        }

    if unverified > 0:
        return {
            "verify": seed_verify_jobs_for_unverified(limit=limit),
            "enrich": 0,
            "qualify": 0,
            "topical": 0,
        }

    if pending_counts.get(AgentJobKind.ENRICH_CONTACT, 0) > 0:
        return {"verify": 0, "enrich": 0, "qualify": 0, "topical": 0}

    if pending_counts.get(AgentJobKind.QUALIFY_CONTACT, 0) > 0:
        return {"verify": 0, "enrich": 0, "qualify": 0, "topical": 0}

    if pending_counts.get(AgentJobKind.CHECK_TOPICAL_RELEVANCE, 0) > 0:
        return {"verify": 0, "enrich": 0, "qualify": 0, "topical": 0}

    if unenriched > 0:
        return {
            "verify": 0,
            "enrich": seed_enrich_jobs_for_unenriched(limit=limit),
            "qualify": 0,
            "topical": 0,
        }

    if unqualified > 0:
        return {
            "verify": 0,
            "enrich": 0,
            "qualify": seed_qualify_jobs_for_unqualified(limit=limit),
            "topical": 0,
        }

    if unchecked_urls > 0:
        return {
            "verify": 0,
            "enrich": 0,
            "qualify": 0,
            "topical": seed_topical_relevance_jobs(limit=limit),
        }

    return empty
