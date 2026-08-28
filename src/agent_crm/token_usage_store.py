"""Persist spark-queue token totals so Live Agents savings survive restarts."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .db import session_scope
from .models import LlmTokenUsage, LlmTokenUsageHour

logger = logging.getLogger(__name__)

_MIN_RATE_HOURS = 1 / 60


def utc_hour_start(now: datetime | None = None) -> datetime:
    """Floor a timestamp to the UTC hour."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def tokens_per_hour(
    prompt_tokens: int,
    completion_tokens: int,
    first_seen_at: datetime | None,
    *,
    now: datetime | None = None,
) -> float:
    """Lifetime average tokens/hour since the actor's first recorded usage."""
    total = max(0, int(prompt_tokens)) + max(0, int(completion_tokens))
    if total <= 0 or first_seen_at is None:
        return 0.0
    moment = now or datetime.now(UTC)
    seen = first_seen_at
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    elapsed_hours = (moment - seen).total_seconds() / 3600.0
    elapsed_hours = max(elapsed_hours, _MIN_RATE_HOURS)
    return round(total / elapsed_hours, 1)


def record_token_usage(
    actor: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    estimated: bool = False,
    now: datetime | None = None,
) -> None:
    """Increment lifetime and current-hour totals for one completion."""
    prompt = max(0, int(prompt_tokens))
    completion = max(0, int(completion_tokens))
    if prompt == 0 and completion == 0:
        return
    label = (actor or "external").strip() or "external"
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    hour_start = utc_hour_start(moment)
    with session_scope() as session:
        row = session.get(LlmTokenUsage, label)
        if row is None:
            session.add(
                LlmTokenUsage(
                    agent_name=label,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    requests=1,
                    estimated_requests=1 if estimated else 0,
                    first_seen_at=moment,
                    last_seen_at=moment,
                )
            )
        else:
            row.prompt_tokens += prompt
            row.completion_tokens += completion
            row.requests += 1
            if estimated:
                row.estimated_requests += 1
            row.last_seen_at = moment

        hour_row = session.get(LlmTokenUsageHour, (label, hour_start))
        if hour_row is None:
            session.add(
                LlmTokenUsageHour(
                    agent_name=label,
                    hour_start=hour_start,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    requests=1,
                )
            )
        else:
            hour_row.prompt_tokens += prompt
            hour_row.completion_tokens += completion
            hour_row.requests += 1


def load_token_usage_snapshot(*, now: datetime | None = None) -> dict[str, Any]:
    """Return persisted usage in the spark-queue health ``token_usage`` shape."""
    moment = now or datetime.now(UTC)
    try:
        with session_scope() as session:
            rows = session.scalars(
                select(LlmTokenUsage).order_by(LlmTokenUsage.agent_name)
            ).all()
            by_actor = {
                row.agent_name: {
                    "prompt_tokens": row.prompt_tokens,
                    "completion_tokens": row.completion_tokens,
                    "requests": row.requests,
                    "estimated_requests": row.estimated_requests,
                    "first_seen_at": row.first_seen_at.isoformat()
                    if row.first_seen_at
                    else None,
                    "last_seen_at": row.last_seen_at.isoformat()
                    if row.last_seen_at
                    else None,
                    "tokens_per_hour": tokens_per_hour(
                        row.prompt_tokens,
                        row.completion_tokens,
                        row.first_seen_at,
                        now=moment,
                    ),
                }
                for row in rows
            }
    except SQLAlchemyError as exc:
        logger.debug("token usage snapshot skipped: %s", exc)
        return {"by_actor": {}, "totals": _empty_totals()}

    return {"by_actor": by_actor, "totals": _totals_from_actors(by_actor, now=moment)}


def _empty_totals() -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "requests": 0,
        "estimated_requests": 0,
        "tokens_per_hour": 0.0,
        "first_seen_at": None,
    }


def _totals_from_actors(
    by_actor: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    prompt = sum(int(row.get("prompt_tokens") or 0) for row in by_actor.values())
    completion = sum(int(row.get("completion_tokens") or 0) for row in by_actor.values())
    first_seen: datetime | None = None
    for row in by_actor.values():
        parsed = _parse_datetime(row.get("first_seen_at"))
        if parsed is not None and (first_seen is None or parsed < first_seen):
            first_seen = parsed
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "requests": sum(int(row.get("requests") or 0) for row in by_actor.values()),
        "estimated_requests": sum(
            int(row.get("estimated_requests") or 0) for row in by_actor.values()
        ),
        "tokens_per_hour": tokens_per_hour(prompt, completion, first_seen, now=now),
        "first_seen_at": first_seen.isoformat() if first_seen else None,
    }


def merge_usage_snapshots(*snapshots: dict[str, Any] | None) -> dict[str, Any]:
    """Field-wise max merge so persisted totals win after a queue restart."""
    by_actor: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        if not snapshot:
            continue
        actors = snapshot.get("by_actor")
        if not isinstance(actors, dict):
            continue
        for actor, raw_row in actors.items():
            if not isinstance(raw_row, dict):
                continue
            existing = by_actor.get(actor)
            by_actor[actor] = _max_actor_row(existing, raw_row) if existing else dict(raw_row)
    moment = datetime.now(UTC)
    for actor, row in by_actor.items():
        row["tokens_per_hour"] = tokens_per_hour(
            int(row.get("prompt_tokens") or 0),
            int(row.get("completion_tokens") or 0),
            _parse_datetime(row.get("first_seen_at")),
            now=moment,
        )
        by_actor[actor] = row
    return {"by_actor": by_actor, "totals": _totals_from_actors(by_actor, now=moment)}


def _max_actor_row(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    first_candidates = [
        parsed
        for parsed in (_parse_datetime(left.get("first_seen_at")), _parse_datetime(right.get("first_seen_at")))
        if parsed is not None
    ]
    last_candidates = [
        parsed
        for parsed in (_parse_datetime(left.get("last_seen_at")), _parse_datetime(right.get("last_seen_at")))
        if parsed is not None
    ]
    first_seen = min(first_candidates) if first_candidates else None
    last_seen = max(last_candidates) if last_candidates else None
    return {
        "prompt_tokens": max(int(left.get("prompt_tokens") or 0), int(right.get("prompt_tokens") or 0)),
        "completion_tokens": max(
            int(left.get("completion_tokens") or 0), int(right.get("completion_tokens") or 0)
        ),
        "requests": max(int(left.get("requests") or 0), int(right.get("requests") or 0)),
        "estimated_requests": max(
            int(left.get("estimated_requests") or 0), int(right.get("estimated_requests") or 0)
        ),
        "first_seen_at": first_seen.isoformat() if first_seen else None,
        "last_seen_at": last_seen.isoformat() if last_seen else None,
    }


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    return None
