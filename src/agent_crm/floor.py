"""Live Agents floor helpers: queue lanes for the Vite dashboard."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from agent_crm.db import session_scope
from agent_crm.engagement.query_store import EngagementQueryStore
from agent_crm.enums import (
    AgentJobStatus,
    EngagementQueryStatus,
    HuntQueryStatus,
    PublishJobStatus,
    ResearchQueryStatus,
    SeoQueryKind,
    SeoQueryStatus,
)
from agent_crm.hunt.store import HuntStore
from agent_crm.jobs.dispatcher import build_job_status
from agent_crm.jobs.store import pending_kind_lag_metrics
from agent_crm.models import AgentJob, EngagementQuery, HuntQuery, PublishJob, ResearchQuery, SeoQuery
from agent_crm.research.query_store import ResearchQueryStore

PROMPT_LIMIT = 4
PROMPT_CHARS = 80
_REVIEW_ORIGIN_PREFIXES = frozenset(
    {"branch", "person", "community", "company", "handle", "engagement", "explicit"}
)
_LANE_ORDER = (
    "research",
    "hunter",
    "engagement",
    "publisher",
    "queue-review",
    "seo",
    "aeo-geo",
    "jobs",
)


def _status_count(status: dict, key: str) -> int:
    nested = status.get("by_status")
    if isinstance(nested, dict):
        return int(nested.get(key) or 0)
    return int(status.get(key) or 0)


def _clip(text: str, *, max_chars: int = PROMPT_CHARS) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1] + "…"


def _age_seconds(created_at: datetime | None) -> int | None:
    if created_at is None:
        return None
    instant = created_at
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - instant).total_seconds()))


def _min_age(ages: list[int | None]) -> int | None:
    present = [age for age in ages if age is not None]
    return min(present) if present else None


def _prompt_label(
    row: Any, *, running_values: frozenset[str], origin_prefix: bool
) -> str | None:
    raw = getattr(row, "query", None) or ""
    label = _clip(str(raw))
    if not label:
        return None
    if origin_prefix:
        origin = str(getattr(row, "origin", "") or "")
        prefix = origin.split(":", 1)[0].strip().lower()
        if prefix in _REVIEW_ORIGIN_PREFIXES and not label.lower().startswith(f"{prefix}:"):
            label = _clip(f"{prefix}: {label}")
    status = getattr(row.status, "value", str(row.status))
    if status in running_values:
        return f"in flight · {label}"
    return label


def _peek(
    session: Session,
    model: type,
    statuses: tuple,
    *,
    extra: Any | None = None,
    running_values: frozenset[str] = frozenset({"running"}),
    origin_prefix: bool = False,
    limit: int = PROMPT_LIMIT,
) -> list[tuple[datetime | None, str]]:
    stmt: Select[Any] = select(model).where(model.status.in_(statuses))
    if extra is not None:
        stmt = stmt.where(extra)
    running_statuses = tuple(
        item for item in statuses if getattr(item, "value", None) == "running"
    )
    if running_statuses:
        rank = case((model.status.in_(running_statuses), 0), else_=1)
        stmt = stmt.order_by(rank, model.created_at.asc(), model.id.asc())
    else:
        stmt = stmt.order_by(model.created_at.asc(), model.id.asc())
    rows = list(session.scalars(stmt.limit(limit)))
    items: list[tuple[datetime | None, str]] = []
    seen: set[str] = set()
    for row in rows:
        label = _prompt_label(
            row, running_values=running_values, origin_prefix=origin_prefix
        )
        if not label or label in seen:
            continue
        seen.add(label)
        items.append((row.created_at, label))
    return items


def _titles(items: list[tuple[datetime | None, str]], *, limit: int = PROMPT_LIMIT) -> list[str]:
    return [label for _created, label in items[:limit]]


def _merge_titles(
    *groups: list[tuple[datetime | None, str]], limit: int = PROMPT_LIMIT
) -> list[str]:
    merged: list[tuple[datetime, str]] = []
    for group in groups:
        for created, label in group:
            stamp = created or datetime.min.replace(tzinfo=UTC)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            merged.append((stamp, label))
    merged.sort(key=lambda item: item[0])
    titles: list[str] = []
    seen: set[str] = set()
    for _stamp, label in merged:
        if label in seen:
            continue
        seen.add(label)
        titles.append(label)
        if len(titles) >= limit:
            break
    return titles


def _oldest(
    session: Session,
    model: type,
    statuses: tuple,
    *,
    extra: Any | None = None,
) -> int | None:
    stmt = select(func.min(model.created_at)).where(model.status.in_(statuses))
    if extra is not None:
        stmt = stmt.where(extra)
    return _age_seconds(session.scalar(stmt))


def _count_status(
    session: Session,
    model: type,
    status: Any,
    *,
    extra: Any | None = None,
) -> int:
    stmt = select(func.count()).select_from(model).where(model.status == status)
    if extra is not None:
        stmt = stmt.where(extra)
    return int(session.scalar(stmt) or 0)


def _job_prompts(
    session: Session, metrics: dict
) -> tuple[list[str], int, int | None]:
    pending_titles = [
        f"{kind.value} × {count}"
        for kind, (count, _oldest_at) in sorted(
            metrics.items(), key=lambda item: (-item[1][0], item[0].value)
        )
    ]
    running_rows = list(
        session.scalars(
            select(AgentJob)
            .where(AgentJob.status == AgentJobStatus.RUNNING)
            .order_by(AgentJob.claimed_at.desc().nullslast(), AgentJob.id.desc())
            .limit(2)
        )
    )
    running = _count_status(session, AgentJob, AgentJobStatus.RUNNING)
    oldest = _oldest(session, AgentJob, (AgentJobStatus.PENDING,))
    inflight = [f"in flight · {row.kind.value}" for row in running_rows]
    combined: list[str] = []
    seen: set[str] = set()
    for item in inflight + pending_titles:
        if item in seen:
            continue
        seen.add(item)
        combined.append(item)
    return combined[:PROMPT_LIMIT], running, oldest


def _lane(
    *,
    id: str,
    name: str,
    agent_name: str,
    pending: int,
    running: int = 0,
    prompts: list[str] | None = None,
    oldest_wait_seconds: int | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "agent_name": agent_name,
        "pending": int(pending),
        "running": int(running),
        "prompts": prompts or [],
        "oldest_wait_seconds": oldest_wait_seconds,
    }


def build_queue_lanes() -> dict:
    """Counts and short prompt previews for the Live Agents task-queue rail."""
    hunt = HuntStore().queue_status()
    research = ResearchQueryStore().queue_status()
    engagement = EngagementQueryStore().queue_status()
    jobs = build_job_status()
    job_metrics = pending_kind_lag_metrics()

    seo_not_aeo = SeoQuery.kind != SeoQueryKind.AEO_GEO
    seo_aeo = SeoQuery.kind == SeoQueryKind.AEO_GEO

    with session_scope() as session:
        research_prompts = _peek(
            session,
            ResearchQuery,
            (ResearchQueryStatus.RUNNING, ResearchQueryStatus.PENDING),
        )
        hunt_prompts = _peek(
            session,
            HuntQuery,
            (HuntQueryStatus.RUNNING, HuntQueryStatus.PENDING),
        )
        engagement_prompts = _peek(
            session,
            EngagementQuery,
            (EngagementQueryStatus.RUNNING, EngagementQueryStatus.PENDING),
        )
        review_prompts = _merge_titles(
            _peek(
                session,
                HuntQuery,
                (HuntQueryStatus.PENDING_REVIEW,),
                running_values=frozenset(),
                origin_prefix=True,
            ),
            _peek(
                session,
                ResearchQuery,
                (ResearchQueryStatus.PENDING_REVIEW,),
                running_values=frozenset(),
                origin_prefix=True,
            ),
            _peek(
                session,
                EngagementQuery,
                (EngagementQueryStatus.PENDING_REVIEW,),
                running_values=frozenset(),
                origin_prefix=True,
            ),
        )
        seo_pending = _count_status(
            session, SeoQuery, SeoQueryStatus.PENDING, extra=seo_not_aeo
        )
        seo_running = _count_status(
            session, SeoQuery, SeoQueryStatus.RUNNING, extra=seo_not_aeo
        )
        aeo_pending = _count_status(
            session, SeoQuery, SeoQueryStatus.PENDING, extra=seo_aeo
        )
        aeo_running = _count_status(
            session, SeoQuery, SeoQueryStatus.RUNNING, extra=seo_aeo
        )
        seo_prompts = _peek(
            session,
            SeoQuery,
            (SeoQueryStatus.RUNNING, SeoQueryStatus.PENDING),
            extra=seo_not_aeo,
        )
        aeo_prompts = _peek(
            session,
            SeoQuery,
            (SeoQueryStatus.RUNNING, SeoQueryStatus.PENDING),
            extra=seo_aeo,
        )
        job_prompts, job_running, job_oldest = _job_prompts(session, job_metrics)
        research_oldest = _oldest(session, ResearchQuery, (ResearchQueryStatus.PENDING,))
        hunt_oldest = _oldest(session, HuntQuery, (HuntQueryStatus.PENDING,))
        engagement_oldest = _oldest(
            session, EngagementQuery, (EngagementQueryStatus.PENDING,)
        )
        publish_pending = _count_status(
            session, PublishJob, PublishJobStatus.SCHEDULED
        )
        publish_running = _count_status(
            session, PublishJob, PublishJobStatus.SENDING
        )
        publish_prompts = [
            _clip(f"{row.platform.value}: {(row.body or '')[:60]}")
            for row in session.scalars(
                select(PublishJob)
                .where(
                    PublishJob.status.in_(
                        (PublishJobStatus.SCHEDULED, PublishJobStatus.SENDING)
                    )
                )
                .order_by(PublishJob.scheduled_at.asc())
                .limit(PROMPT_LIMIT)
            )
        ]
        publish_oldest = _oldest(
            session, PublishJob, (PublishJobStatus.SCHEDULED,)
        )
        review_oldest = _min_age(
            [
                _oldest(session, HuntQuery, (HuntQueryStatus.PENDING_REVIEW,)),
                _oldest(session, ResearchQuery, (ResearchQueryStatus.PENDING_REVIEW,)),
                _oldest(
                    session, EngagementQuery, (EngagementQueryStatus.PENDING_REVIEW,)
                ),
            ]
        )
        seo_oldest = _oldest(
            session, SeoQuery, (SeoQueryStatus.PENDING,), extra=seo_not_aeo
        )
        aeo_oldest = _oldest(
            session, SeoQuery, (SeoQueryStatus.PENDING,), extra=seo_aeo
        )

    review_pending = (
        _status_count(hunt, "pending_review")
        + int(research.get("pending_review") or 0)
        + int(engagement.get("pending_review") or 0)
    )

    lanes = [
        _lane(
            id="research",
            name="Research topics",
            agent_name="research",
            pending=int(research.get("pending") or 0),
            running=int(research.get("running") or 0),
            prompts=_titles(research_prompts),
            oldest_wait_seconds=research_oldest,
        ),
        _lane(
            id="hunter",
            name="Hunter queries",
            agent_name="outbound_hunter",
            pending=int(hunt.get("pending") or 0),
            running=_status_count(hunt, "running"),
            prompts=_titles(hunt_prompts),
            oldest_wait_seconds=hunt_oldest,
        ),
        _lane(
            id="engagement",
            name="Comment topics",
            agent_name="engagement",
            pending=int(engagement.get("pending") or 0),
            running=int(engagement.get("running") or 0),
            prompts=_titles(engagement_prompts),
            oldest_wait_seconds=engagement_oldest,
        ),
        _lane(
            id="publisher",
            name="Publish queue",
            agent_name="publisher",
            pending=publish_pending,
            running=publish_running,
            prompts=publish_prompts,
            oldest_wait_seconds=publish_oldest,
        ),
        _lane(
            id="queue-review",
            name="Queue review",
            agent_name="queue-review",
            pending=review_pending,
            prompts=review_prompts,
            oldest_wait_seconds=review_oldest,
        ),
        _lane(
            id="seo",
            name="SEO documents",
            agent_name="seo",
            pending=seo_pending,
            running=seo_running,
            prompts=_titles(seo_prompts),
            oldest_wait_seconds=seo_oldest,
        ),
        _lane(
            id="aeo-geo",
            name="AEO / GEO",
            agent_name="aeo-geo",
            pending=aeo_pending,
            running=aeo_running,
            prompts=_titles(aeo_prompts),
            oldest_wait_seconds=aeo_oldest,
        ),
        _lane(
            id="jobs",
            name="Contact jobs",
            agent_name="job-dispatcher",
            pending=int(jobs.get("pending_total") or 0),
            running=job_running,
            prompts=job_prompts,
            oldest_wait_seconds=job_oldest,
        ),
    ]
    order = {lane_id: index for index, lane_id in enumerate(_LANE_ORDER)}
    lanes.sort(key=lambda lane: (-int(lane["pending"]), order.get(lane["id"], 99)))
    return {"waiting": sum(int(lane["pending"]) for lane in lanes), "lanes": lanes}
