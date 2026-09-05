"""Standing publisher loop: drain due publish_jobs after human schedule.

Draft agents never call adapters. This worker is the only send path.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from agent_crm.agent_control import stop_if_disabled, wait_while_disabled
from agent_crm.config import get_settings
from agent_crm.enums import AgentStatus, Brand, PublishJobStatus
from agent_crm.heartbeat import record_heartbeat
from agent_crm.publish.adapters import (
    PublishAdapterError,
    RateLimitedError,
    adapter_for,
)
from agent_crm.publish.store import (
    claim_due_jobs,
    count_publish_jobs,
    mark_job_failed,
    mark_job_posted,
    next_slot_for_account,
)

logger = logging.getLogger(__name__)

ACTOR = "publisher"
WATCH_POLL_SECONDS = 30.0


@dataclass
class PublishBudget:
    max_jobs: int = 5

    def __post_init__(self) -> None:
        settings = get_settings()
        self.max_jobs = min(self.max_jobs, settings.publish_max_jobs_per_cycle)


@dataclass
class PublishLoopResult:
    claimed: int = 0
    posted: int = 0
    failed: int = 0
    rescheduled: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    stop_reason: str = "ok"


def _tactic_blocked(job) -> bool:
    settings = get_settings()
    if job.brand != Brand.TACTIC_STUDIO:
        return False
    return not (job.pete_override or settings.publish_allow_tactic_studio)


def _proof_blocked(body: str) -> bool:
    return "[NEED:" in (body or "")


def _account_rate_limited(account) -> datetime | None:
    """Return a future time if the account is over cap / interval, else None."""
    now = datetime.now(UTC)
    slot = next_slot_for_account(account, now=now)
    if slot > now + timedelta(seconds=5):
        return slot
    return None


def run_publish_loop(*, budget: PublishBudget | None = None) -> PublishLoopResult:
    budget = budget or PublishBudget()
    result = PublishLoopResult()
    if stop_if_disabled(ACTOR):
        result.stop_reason = "disabled"
        record_heartbeat(ACTOR, status=AgentStatus.IDLE, task="paused")
        return result

    settings = get_settings()
    record_heartbeat(
        ACTOR,
        status=AgentStatus.WORKING,
        task=f"claiming up to {budget.max_jobs} due jobs",
    )
    jobs = claim_due_jobs(limit=budget.max_jobs)
    result.claimed = len(jobs)
    if not jobs:
        result.stop_reason = "idle"
        record_heartbeat(
            ACTOR,
            status=AgentStatus.IDLE,
            task=f"scheduled={count_publish_jobs(status=PublishJobStatus.SCHEDULED)}",
        )
        return result

    for job in jobs:
        if stop_if_disabled(ACTOR):
            mark_job_failed(
                job.id,
                error="publisher disabled mid-cycle",
                reschedule_at=datetime.now(UTC) + timedelta(minutes=1),
            )
            result.rescheduled += 1
            result.stop_reason = "disabled"
            break

        account = job.account
        if account is None or not account.enabled:
            mark_job_failed(job.id, error="social account missing or disabled")
            result.failed += 1
            result.errors.append(f"job {job.id}: account disabled")
            continue

        if _tactic_blocked(job):
            mark_job_failed(
                job.id,
                error="tactic.studio blocked without pete_override / allow flag",
            )
            result.failed += 1
            result.errors.append(f"job {job.id}: tactic.studio gated")
            continue

        if _proof_blocked(job.body):
            mark_job_failed(job.id, error="body contains [NEED: proof placeholders]")
            result.failed += 1
            result.errors.append(f"job {job.id}: proof gate")
            continue

        rate_wait = _account_rate_limited(account)
        if rate_wait is not None:
            mark_job_failed(
                job.id,
                error="account rate limit / daily cap",
                reschedule_at=rate_wait,
            )
            result.rescheduled += 1
            continue

        try:
            adapter = adapter_for(
                job,
                account,
                force_dry_run=settings.publish_dry_run,
            )
            outcome = adapter.publish(job, account)
            mark_job_posted(
                job.id,
                posted_url=outcome.posted_url,
                platform_post_id=outcome.platform_post_id,
            )
            result.posted += 1
            logger.info(
                "publish ok job=%s dry_run=%s post_id=%s",
                job.id,
                outcome.dry_run,
                outcome.platform_post_id,
            )
        except RateLimitedError as exc:
            wait = datetime.now(UTC) + timedelta(seconds=exc.retry_after_seconds)
            mark_job_failed(job.id, error=str(exc), reschedule_at=wait)
            result.rescheduled += 1
            result.errors.append(f"job {job.id}: {exc}")
        except PublishAdapterError as exc:
            mark_job_failed(job.id, error=str(exc))
            result.failed += 1
            result.errors.append(f"job {job.id}: {exc}")
        except Exception as exc:  # noqa: BLE001 — keep loop alive
            mark_job_failed(job.id, error=f"unexpected: {exc}")
            result.failed += 1
            result.errors.append(f"job {job.id}: {exc}")
            logger.exception("publish job %s failed", job.id)

    record_heartbeat(
        ACTOR,
        status=AgentStatus.IDLE,
        task=(
            f"posted={result.posted} failed={result.failed} "
            f"rescheduled={result.rescheduled}"
        ),
    )
    return result


def run_publish_loop_watch(*, budget: PublishBudget | None = None) -> None:
    settings = get_settings()
    poll = float(settings.publish_poll_seconds or WATCH_POLL_SECONDS)
    while True:
        wait_while_disabled(ACTOR)
        result = run_publish_loop(budget=budget)
        if result.stop_reason == "disabled":
            time.sleep(poll)
            continue
        if result.claimed == 0:
            record_heartbeat(ACTOR, status=AgentStatus.IDLE, task="waiting for due jobs")
            time.sleep(poll)
            continue
        # Brief pause between cycles when work was done.
        time.sleep(min(poll, 5.0))
