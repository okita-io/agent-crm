"""Persistence for handle-keyed comment authors (no email required)."""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from .comment_extractor import ExtractedCommentPerson, extract_comment_people
from .config import get_settings
from .contact_store import ContactExtractionBudget
from .db import session_scope
from .enums import Brand, ContactAudience
from .models import CommentPerson
from .schemas import CommentPersonOut

logger = logging.getLogger(__name__)


def merge_comment_snippets(
    existing: list | None,
    *,
    source_url: str,
    snippet: str | None,
) -> list[dict]:
    entries = list(existing or [])
    if not snippet:
        return entries
    for item in entries:
        if (
            isinstance(item, dict)
            and item.get("source_url") == source_url
            and item.get("snippet") == snippet
        ):
            return entries
    entries.append({"source_url": source_url, "snippet": snippet[:240]})
    return entries[-10:]


def merge_source_urls(existing: list | None, source_url: str) -> list[str]:
    urls = list(existing or [])
    if source_url not in urls:
        urls.append(source_url)
    return urls


def upsert_comment_person(
    *,
    platform: str,
    handle: str,
    brand: Brand,
    source_url: str,
    display_name: str | None = None,
    profile_url: str | None = None,
    comment_snippet: str | None = None,
    audience: ContactAudience | None = None,
) -> CommentPersonOut:
    """Insert or merge a handle-keyed comment author."""
    normalized_platform = platform.strip().lower()[:64]
    normalized_handle = handle.strip().lower()[:128]

    with session_scope() as session:
        row = session.scalar(
            select(CommentPerson).where(
                CommentPerson.platform == normalized_platform,
                CommentPerson.handle == normalized_handle,
            )
        )
        if row is None:
            row = CommentPerson(
                platform=normalized_platform,
                handle=normalized_handle,
                display_name=display_name,
                profile_url=profile_url,
                brand=brand,
                audience=audience,
                source_urls=[source_url],
                comment_snippets=merge_comment_snippets(
                    None,
                    source_url=source_url,
                    snippet=comment_snippet,
                ),
            )
            session.add(row)
        else:
            if display_name and not row.display_name:
                row.display_name = display_name
            if profile_url and not row.profile_url:
                row.profile_url = profile_url
            if brand != Brand.UNASSIGNED and row.brand == Brand.UNASSIGNED:
                row.brand = brand
            if audience is not None:
                if row.audience is None or (
                    audience == ContactAudience.INFLUENCER
                    and row.audience != ContactAudience.INFLUENCER
                ):
                    row.audience = audience
            row.source_urls = merge_source_urls(row.source_urls, source_url)
            row.comment_snippets = merge_comment_snippets(
                row.comment_snippets,
                source_url=source_url,
                snippet=comment_snippet,
            )

        session.flush()
        return CommentPersonOut.model_validate(row)


def _apply_comment_person_filters(
    stmt,
    *,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    platform: str | None = None,
):
    if brand is not None:
        stmt = stmt.where(CommentPerson.brand == brand)
    if audience is not None:
        stmt = stmt.where(CommentPerson.audience == audience)
    if platform is not None:
        stmt = stmt.where(CommentPerson.platform == platform.strip().lower())
    return stmt


def count_comment_people(
    *,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    platform: str | None = None,
) -> int:
    with session_scope() as session:
        stmt = select(func.count()).select_from(CommentPerson)
        stmt = _apply_comment_person_filters(
            stmt,
            brand=brand,
            audience=audience,
            platform=platform,
        )
        return int(session.scalar(stmt) or 0)


def list_comment_people(
    *,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    platform: str | None = None,
    offset: int = 0,
    limit: int | None = 500,
) -> list[CommentPersonOut]:
    with session_scope() as session:
        stmt = select(CommentPerson).order_by(CommentPerson.updated_at.desc())
        stmt = _apply_comment_person_filters(
            stmt,
            brand=brand,
            audience=audience,
            platform=platform,
        )
        stmt = stmt.offset(max(offset, 0))
        if limit is not None:
            stmt = stmt.limit(limit)
        return [CommentPersonOut.model_validate(row) for row in session.scalars(stmt)]


def process_scraped_page_comment_people(
    *,
    markdown: str | None,
    source_url: str,
    brand: Brand,
    html: str | None = None,
    audience: ContactAudience | None = None,
    budget: ContactExtractionBudget | None = None,
) -> list[CommentPersonOut]:
    """Extract comment authors from a scraped page and upsert handle-keyed rows."""
    settings = get_settings()
    try:
        extracted = extract_comment_people(
            markdown=markdown,
            html=html,
            source_url=source_url,
            max_handles=settings.comment_people_per_page,
            budget=budget,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Comment people extraction failed for %s", source_url)
        return []

    profiles: list[CommentPersonOut] = []
    for person in extracted:
        try:
            profile = upsert_comment_person(
                platform=person.platform,
                handle=person.handle,
                brand=brand,
                source_url=source_url,
                display_name=person.display_name,
                profile_url=person.profile_url,
                comment_snippet=person.comment_snippet,
                audience=audience or person.audience,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to upsert comment person %s/%s",
                person.platform,
                person.handle,
            )
            continue
        profiles.append(profile)
    return profiles
