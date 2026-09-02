"""Persist and backfill topical relevance verdicts for stored URLs."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from .db import session_scope
from .enums import Brand, LeadStatus, TopicalRelevanceVerdict
from .hunt_relevance import (
    RelevanceAssessment,
    assess_topical_relevance,
    fetch_public_page_excerpt,
)
from .llm_text import sanitize_postgres_text
from .models import (
    CommentPerson,
    ContactProfile,
    HuntResource,
    Lead,
    ResearchFinding,
    UrlTopicRelevance,
)

logger = logging.getLogger(__name__)


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return url.strip()
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def _assessment_for_persist(assessment: RelevanceAssessment) -> RelevanceAssessment:
    """Sanitize LLM/page text before persisting to PostgreSQL text columns."""
    reason = sanitize_postgres_text(assessment.reason)
    if not reason:
        return RelevanceAssessment(
            verdict=TopicalRelevanceVerdict.UNCERTAIN,
            reason="insufficient classification detail after sanitization",
            spark_used=assessment.spark_used,
        )
    if reason == assessment.reason:
        return assessment
    return RelevanceAssessment(
        verdict=assessment.verdict,
        reason=reason,
        spark_used=assessment.spark_used,
    )


def upsert_url_topic_relevance(
    *,
    url: str,
    brand: Brand,
    assessment: RelevanceAssessment,
    source_kind: str | None = None,
    source_id: int | None = None,
    page_title: str | None = None,
    page_excerpt: str | None = None,
) -> UrlTopicRelevance:
    normalized = normalize_url(url)
    assessment = _assessment_for_persist(assessment)
    safe_source_kind = sanitize_postgres_text(source_kind)
    safe_page_title = sanitize_postgres_text(page_title)
    safe_page_excerpt = sanitize_postgres_text(page_excerpt)
    if safe_page_excerpt:
        safe_page_excerpt = safe_page_excerpt[:4000]
    checked_at = datetime.now(UTC)
    with session_scope() as session:
        row = session.scalar(
            select(UrlTopicRelevance).where(
                UrlTopicRelevance.url == normalized,
                UrlTopicRelevance.brand == brand,
            )
        )
        if row is None:
            row = UrlTopicRelevance(
                url=normalized,
                brand=brand,
                verdict=assessment.verdict,
                reason=assessment.reason,
                checked_at=checked_at,
                source_kind=safe_source_kind,
                source_id=source_id,
                page_title=safe_page_title,
                page_excerpt=safe_page_excerpt,
            )
            session.add(row)
        else:
            row.verdict = assessment.verdict
            row.reason = assessment.reason
            row.checked_at = checked_at
            if safe_source_kind:
                row.source_kind = safe_source_kind
            if source_id is not None:
                row.source_id = source_id
            if safe_page_title:
                row.page_title = safe_page_title
            if safe_page_excerpt:
                row.page_excerpt = safe_page_excerpt
        session.flush()
        return row


def get_url_verdict(url: str, brand: Brand) -> TopicalRelevanceVerdict | None:
    normalized = normalize_url(url)
    with session_scope() as session:
        row = session.scalar(
            select(UrlTopicRelevance.verdict).where(
                UrlTopicRelevance.url == normalized,
                UrlTopicRelevance.brand == brand,
            )
        )
        return row


def lead_source_urls(lead: Lead) -> list[str]:
    urls: list[str] = []
    payload = lead.raw_payload if isinstance(lead.raw_payload, dict) else {}
    for key in ("url", "website", "contact_url", "page_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            urls.append(value.strip())
    found_on = payload.get("found_on")
    if isinstance(found_on, list):
        urls.extend(item for item in found_on if isinstance(item, str) and item.strip())
    return urls


def lead_is_topically_visible(lead: Lead) -> bool:
    """Return False when any known source URL is off-topic or uncertain for the brand."""
    if lead.brand == Brand.UNASSIGNED:
        return True
    urls = lead_source_urls(lead)
    if not urls:
        return True
    with session_scope() as session:
        for url in urls:
            normalized = normalize_url(url)
            verdict = session.scalar(
                select(UrlTopicRelevance.verdict).where(
                    UrlTopicRelevance.url == normalized,
                    UrlTopicRelevance.brand == lead.brand,
                )
            )
            if verdict in {
                TopicalRelevanceVerdict.OFF_TOPIC,
                TopicalRelevanceVerdict.UNCERTAIN,
            }:
                return False
    return True


def apply_off_topic_lead_consequences(*, url: str, brand: Brand) -> int:
    """Disqualify leads tied to an off-topic URL without deleting history."""
    normalized = normalize_url(url)
    updated = 0
    with session_scope() as session:
        leads = list(
            session.scalars(
                select(Lead).where(Lead.brand == brand).where(
                    Lead.status != LeadStatus.DISQUALIFIED
                )
            )
        )
        for lead in leads:
            source_urls = [normalize_url(item) for item in lead_source_urls(lead)]
            if normalized not in source_urls:
                continue
            lead.status = LeadStatus.DISQUALIFIED
            raw = dict(lead.raw_payload or {})
            raw["topical_relevance"] = {
                "url": normalized,
                "verdict": TopicalRelevanceVerdict.OFF_TOPIC.value,
            }
            lead.raw_payload = raw
            updated += 1
    return updated


def check_topical_relevance_job(
    *,
    url: str,
    brand: Brand,
    source_kind: str | None = None,
    source_id: int | None = None,
    query: str | None = None,
    allow_spark: bool = True,
    client: httpx.Client | None = None,
) -> RelevanceAssessment:
    """Fetch a stored URL and persist a topical verdict."""
    page_title, page_excerpt, _status = fetch_public_page_excerpt(url, client=client)
    assessment = assess_topical_relevance(
        brand=brand,
        url=url,
        title=page_title,
        snippet=None,
        page_excerpt=page_excerpt,
        query=query,
        allow_spark=allow_spark,
    )
    upsert_url_topic_relevance(
        url=url,
        brand=brand,
        assessment=assessment,
        source_kind=source_kind,
        source_id=source_id,
        page_title=page_title,
        page_excerpt=page_excerpt,
    )
    if assessment.verdict == TopicalRelevanceVerdict.OFF_TOPIC:
        apply_off_topic_lead_consequences(url=url, brand=brand)
    return assessment


def iter_stored_url_candidates(*, limit: int = 500) -> list[dict[str, Any]]:
    """Collect URL/brand pairs from existing CRM rows lacking a verdict."""
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(url: str, brand: Brand, *, source_kind: str, source_id: int) -> None:
        if brand == Brand.UNASSIGNED:
            return
        normalized = normalize_url(url)
        key = (normalized, brand.value)
        if key in seen:
            return
        seen.add(key)
        candidates.append(
            {
                "url": normalized,
                "brand": brand,
                "source_kind": source_kind,
                "source_id": source_id,
            }
        )

    with session_scope() as session:
        existing = {
            (row.url, row.brand.value)
            for row in session.scalars(select(UrlTopicRelevance))
        }

    with session_scope() as session:
        for row in session.scalars(select(HuntResource).limit(limit)):
            key = (normalize_url(row.url), row.brand.value)
            if key not in existing:
                add(row.url, row.brand, source_kind="hunt_resource", source_id=row.id)

        for row in session.scalars(select(ContactProfile).limit(limit)):
            for url in row.source_urls or []:
                if isinstance(url, str):
                    key = (normalize_url(url), row.brand.value)
                    if key not in existing:
                        add(url, row.brand, source_kind="contact_profile", source_id=row.id)

        for row in session.scalars(select(CommentPerson).limit(limit)):
            for url in row.source_urls or []:
                if isinstance(url, str):
                    key = (normalize_url(url), row.brand.value)
                    if key not in existing:
                        add(url, row.brand, source_kind="comment_person", source_id=row.id)

        for row in session.scalars(select(ResearchFinding).limit(limit)):
            key = (normalize_url(row.url), row.brand.value)
            if key not in existing:
                add(row.url, row.brand, source_kind="research_finding", source_id=row.id)

        for row in session.scalars(select(Lead).limit(limit)):
            if row.brand == Brand.UNASSIGNED:
                continue
            for url in lead_source_urls(row):
                key = (normalize_url(url), row.brand.value)
                if key not in existing:
                    add(url, row.brand, source_kind="lead", source_id=row.id)

    return candidates[:limit]


def count_urls_needing_topical_check() -> int:
    return len(iter_stored_url_candidates(limit=500))


def seed_topical_relevance_jobs(*, limit: int = 50) -> int:
    from .job_store import enqueue_topical_relevance_job

    enqueued = 0
    for candidate in iter_stored_url_candidates(limit=limit * 3):
        if enqueued >= limit:
            break
        if enqueue_topical_relevance_job(
            url=candidate["url"],
            brand=candidate["brand"],
            source_kind=candidate["source_kind"],
            source_id=candidate["source_id"],
        ):
            enqueued += 1
    return enqueued
