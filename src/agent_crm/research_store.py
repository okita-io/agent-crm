"""Persistence helpers for research findings."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from .db import session_scope
from .enums import Brand, ResearchFindingKind
from .models import ResearchFinding
from .research_utils import canonical_url, extract_domain
from .schemas import ResearchFindingOut


def upsert_finding(
    *,
    url: str,
    title: str,
    brand: Brand,
    kind: ResearchFindingKind,
    summary: str,
    source_query: str,
    raw_snippet: str | None = None,
    extra: dict | None = None,
) -> ResearchFindingOut:
    """Insert or update a finding keyed by canonical URL."""
    normalized = canonical_url(url)
    now = datetime.now(UTC)
    with session_scope() as session:
        row = session.scalar(
            select(ResearchFinding).where(
                ResearchFinding.url == normalized,
                ResearchFinding.brand == brand,
            )
        )
        if row is None:
            row = ResearchFinding(
                url=normalized,
                domain=extract_domain(normalized),
                title=title,
                brand=brand,
                kind=kind,
                summary=summary,
                source_query=source_query,
                raw_snippet=raw_snippet,
                extra=extra,
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(row)
        else:
            row.title = title
            row.kind = kind
            row.summary = summary
            row.source_query = source_query
            row.raw_snippet = raw_snippet
            row.extra = extra
            row.last_seen_at = now
        session.flush()
        return ResearchFindingOut.model_validate(row)


def list_findings(
    *,
    brand: Brand | None = None,
    kind: ResearchFindingKind | None = None,
    limit: int = 200,
) -> list[ResearchFindingOut]:
    with session_scope() as session:
        stmt = select(ResearchFinding).order_by(ResearchFinding.last_seen_at.desc())
        if brand is not None:
            stmt = stmt.where(ResearchFinding.brand == brand)
        if kind is not None:
            stmt = stmt.where(ResearchFinding.kind == kind)
        stmt = stmt.limit(limit)
        return [ResearchFindingOut.model_validate(row) for row in session.scalars(stmt)]
