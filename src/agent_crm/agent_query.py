"""Read-only Hermes query helpers for collected CRM data."""

from __future__ import annotations

from sqlalchemy import func, or_, select

from .comment_people_store import count_comment_people, list_comment_people
from .contact_quality import EmailQualityFilter
from .contact_store import count_contact_profiles, list_contact_profiles
from .db import session_scope
from .enums import (
    Brand,
    ContactAudience,
    ContactEmailKind,
    ContactKind,
    ContactVerificationStatus,
    HuntResourceKind,
    ResearchFindingKind,
)
from .models import (
    CommentPerson,
    ContactProfile,
    ContactVerification,
    EngagementThread,
    HuntResource,
    ResearchFinding,
)
from .pipeline_leads import list_pipeline_leads
from .schemas import (
    AgentCatalogOut,
    AgentPageOut,
    AgentSearchHitOut,
    AgentSearchOut,
    CommentPersonOut,
    ContactProfileOut,
    EngagementThreadOut,
    HuntResourceOut,
    ResearchFindingOut,
)


def _contains(column, q: str):
    """Case-insensitive substring match that treats ``%``/``_`` as literals."""
    needle = q.strip().lower()
    escaped = needle.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return func.lower(column).like(f"%{escaped}%", escape="\\")


def agent_catalog() -> AgentCatalogOut:
    return AgentCatalogOut(
        collections=[
            "contacts",
            "websites",
            "findings",
            "comment-people",
            "pipeline-leads",
            "engagement-threads",
        ],
        brands=[member.value for member in Brand],
        audiences=[member.value for member in ContactAudience],
        resource_kinds=[member.value for member in HuntResourceKind],
        finding_kinds=[member.value for member in ResearchFindingKind],
        email_kinds=[member.value for member in ContactEmailKind],
    )


def _page(items: list, *, total: int, offset: int, limit: int) -> AgentPageOut:
    return AgentPageOut(items=items, total=total, offset=offset, limit=limit)


def query_contacts(
    *,
    q: str | None = None,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    quality: EmailQualityFilter = "all",
    verified: bool | None = None,
    offset: int = 0,
    limit: int = 50,
) -> AgentPageOut:
    if not q and verified is None:
        total = count_contact_profiles(brand=brand, audience=audience, quality=quality)
        items = list_contact_profiles(
            brand=brand,
            audience=audience,
            quality=quality,
            offset=offset,
            limit=limit,
        )
        return _page(items, total=total, offset=offset, limit=limit)

    with session_scope() as session:
        stmt = select(ContactProfile).order_by(ContactProfile.updated_at.desc())
        count_stmt = select(func.count()).select_from(ContactProfile)
        if brand is not None:
            stmt = stmt.where(ContactProfile.brand == brand)
            count_stmt = count_stmt.where(ContactProfile.brand == brand)
        if audience is not None:
            stmt = stmt.where(ContactProfile.audience == audience)
            count_stmt = count_stmt.where(ContactProfile.audience == audience)
        if quality == "person":
            stmt = stmt.where(ContactProfile.email_kind == ContactEmailKind.PERSON)
            count_stmt = count_stmt.where(
                ContactProfile.email_kind == ContactEmailKind.PERSON
            )
        elif quality == "role":
            stmt = stmt.where(ContactProfile.email_kind == ContactEmailKind.ROLE)
            count_stmt = count_stmt.where(
                ContactProfile.email_kind == ContactEmailKind.ROLE
            )
        if q:
            needle = q.strip()
            clause = or_(
                _contains(ContactProfile.email, needle),
                _contains(ContactProfile.name, needle),
                _contains(ContactProfile.organization, needle),
            )
            stmt = stmt.where(clause)
            count_stmt = count_stmt.where(clause)
        if verified is True:
            valid = (
                select(ContactVerification.lead_id)
                .where(ContactVerification.contact_kind == ContactKind.EMAIL)
                .where(ContactVerification.status == ContactVerificationStatus.VALID)
                .where(
                    func.lower(func.trim(ContactVerification.contact))
                    == func.lower(func.trim(ContactProfile.email))
                )
                .correlate(ContactProfile)
                .exists()
            )
            stmt = stmt.where(ContactProfile.lead_id.is_not(None)).where(valid)
            count_stmt = count_stmt.where(ContactProfile.lead_id.is_not(None)).where(
                valid
            )
        total = int(session.scalar(count_stmt) or 0)
        rows = list(session.scalars(stmt.offset(offset).limit(limit)))
        items = [ContactProfileOut.model_validate(row) for row in rows]
        return _page(items, total=total, offset=offset, limit=limit)


def get_contact(contact_id: int) -> ContactProfileOut | None:
    with session_scope() as session:
        row = session.get(ContactProfile, contact_id)
        if row is None:
            return None
        return ContactProfileOut.model_validate(row)


def query_websites(
    *,
    q: str | None = None,
    brand: Brand | None = None,
    kind: HuntResourceKind | None = None,
    domain: str | None = None,
    url: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> AgentPageOut:
    with session_scope() as session:
        stmt = select(HuntResource).order_by(HuntResource.last_seen.desc())
        count_stmt = select(func.count()).select_from(HuntResource)
        if brand is not None:
            stmt = stmt.where(HuntResource.brand == brand)
            count_stmt = count_stmt.where(HuntResource.brand == brand)
        if kind is not None:
            stmt = stmt.where(HuntResource.kind == kind)
            count_stmt = count_stmt.where(HuntResource.kind == kind)
        if domain is not None:
            stmt = stmt.where(HuntResource.domain == domain.strip().lower())
            count_stmt = count_stmt.where(HuntResource.domain == domain.strip().lower())
        if url is not None:
            stmt = stmt.where(HuntResource.url == url.strip())
            count_stmt = count_stmt.where(HuntResource.url == url.strip())
        if q:
            needle = q.strip()
            clause = or_(
                _contains(HuntResource.url, needle),
                _contains(HuntResource.domain, needle),
                _contains(HuntResource.title, needle),
                _contains(HuntResource.notes, needle),
            )
            stmt = stmt.where(clause)
            count_stmt = count_stmt.where(clause)
        total = int(session.scalar(count_stmt) or 0)
        rows = list(session.scalars(stmt.offset(offset).limit(limit)))
        items = [HuntResourceOut.model_validate(row) for row in rows]
        return _page(items, total=total, offset=offset, limit=limit)


def get_website(resource_id: int) -> HuntResourceOut | None:
    with session_scope() as session:
        row = session.get(HuntResource, resource_id)
        if row is None:
            return None
        return HuntResourceOut.model_validate(row)


def query_findings(
    *,
    q: str | None = None,
    brand: Brand | None = None,
    kind: ResearchFindingKind | None = None,
    offset: int = 0,
    limit: int = 50,
) -> AgentPageOut:
    with session_scope() as session:
        stmt = select(ResearchFinding).order_by(ResearchFinding.last_seen_at.desc())
        count_stmt = select(func.count()).select_from(ResearchFinding)
        if brand is not None:
            stmt = stmt.where(ResearchFinding.brand == brand)
            count_stmt = count_stmt.where(ResearchFinding.brand == brand)
        if kind is not None:
            stmt = stmt.where(ResearchFinding.kind == kind)
            count_stmt = count_stmt.where(ResearchFinding.kind == kind)
        if q:
            needle = q.strip()
            clause = or_(
                _contains(ResearchFinding.url, needle),
                _contains(ResearchFinding.domain, needle),
                _contains(ResearchFinding.title, needle),
                _contains(ResearchFinding.summary, needle),
            )
            stmt = stmt.where(clause)
            count_stmt = count_stmt.where(clause)
        total = int(session.scalar(count_stmt) or 0)
        rows = list(session.scalars(stmt.offset(offset).limit(limit)))
        items = [ResearchFindingOut.model_validate(row) for row in rows]
        return _page(items, total=total, offset=offset, limit=limit)


def get_finding(finding_id: int) -> ResearchFindingOut | None:
    with session_scope() as session:
        row = session.get(ResearchFinding, finding_id)
        if row is None:
            return None
        return ResearchFindingOut.model_validate(row)


def query_comment_people(
    *,
    q: str | None = None,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    platform: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> AgentPageOut:
    if not q:
        total = count_comment_people(brand=brand, audience=audience, platform=platform)
        items = list_comment_people(
            brand=brand,
            audience=audience,
            platform=platform,
            offset=offset,
            limit=limit,
        )
        return _page(items, total=total, offset=offset, limit=limit)

    with session_scope() as session:
        stmt = select(CommentPerson).order_by(CommentPerson.updated_at.desc())
        count_stmt = select(func.count()).select_from(CommentPerson)
        if brand is not None:
            stmt = stmt.where(CommentPerson.brand == brand)
            count_stmt = count_stmt.where(CommentPerson.brand == brand)
        if audience is not None:
            stmt = stmt.where(CommentPerson.audience == audience)
            count_stmt = count_stmt.where(CommentPerson.audience == audience)
        if platform is not None:
            stmt = stmt.where(CommentPerson.platform == platform.strip().lower())
            count_stmt = count_stmt.where(
                CommentPerson.platform == platform.strip().lower()
            )
        needle = q.strip()
        clause = or_(
            _contains(CommentPerson.handle, needle),
            _contains(CommentPerson.display_name, needle),
        )
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)
        total = int(session.scalar(count_stmt) or 0)
        rows = list(session.scalars(stmt.offset(offset).limit(limit)))
        items = [CommentPersonOut.model_validate(row) for row in rows]
        return _page(items, total=total, offset=offset, limit=limit)


def query_pipeline_leads(
    *,
    q: str | None = None,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    offset: int = 0,
    limit: int = 50,
) -> AgentPageOut:
    # Pull a bounded verified set then filter/page in Python for q support.
    # Pipeline visibility already requires VALID verification.
    fetch_limit = 5000 if q else max(limit + offset, 500)
    leads = list_pipeline_leads(brand=brand, audience=audience, limit=fetch_limit)
    if q:
        needle = q.strip().lower()
        leads = [
            lead
            for lead in leads
            if needle in (lead.email or "").lower()
            or needle in (lead.name or "").lower()
            or needle in (lead.company or "").lower()
        ]
    total = len(leads)
    page = leads[offset : offset + limit]
    return _page(page, total=total, offset=offset, limit=limit)


def query_engagement_threads(
    *,
    q: str | None = None,
    brand: Brand | None = None,
    offset: int = 0,
    limit: int = 50,
) -> AgentPageOut:
    with session_scope() as session:
        stmt = select(EngagementThread).order_by(
            EngagementThread.popularity_score.desc(),
            EngagementThread.updated_at.desc(),
        )
        count_stmt = select(func.count()).select_from(EngagementThread)
        if brand is not None:
            stmt = stmt.where(EngagementThread.brand == brand)
            count_stmt = count_stmt.where(EngagementThread.brand == brand)
        if q:
            needle = q.strip()
            clause = or_(
                _contains(EngagementThread.url, needle),
                _contains(EngagementThread.title, needle),
                _contains(EngagementThread.excerpt, needle),
            )
            stmt = stmt.where(clause)
            count_stmt = count_stmt.where(clause)
        total = int(session.scalar(count_stmt) or 0)
        rows = list(session.scalars(stmt.offset(offset).limit(limit)))
        items = [EngagementThreadOut.model_validate(row) for row in rows]
        return _page(items, total=total, offset=offset, limit=limit)


def agent_search(q: str, *, per_collection: int = 10) -> AgentSearchOut:
    needle = q.strip()
    if not needle:
        return AgentSearchOut(q=q, hits=[])
    hits: list[AgentSearchHitOut] = []

    contacts = query_contacts(q=needle, limit=per_collection)
    for item in contacts.items:
        assert isinstance(item, ContactProfileOut)
        hits.append(
            AgentSearchHitOut(
                collection="contacts",
                id=item.id,
                title=item.name or item.email,
                email=item.email,
                brand=item.brand,
            )
        )

    websites = query_websites(q=needle, limit=per_collection)
    for item in websites.items:
        assert isinstance(item, HuntResourceOut)
        hits.append(
            AgentSearchHitOut(
                collection="websites",
                id=item.id,
                title=item.title or item.domain,
                url=item.url,
                brand=item.brand,
            )
        )

    findings = query_findings(q=needle, limit=per_collection)
    for item in findings.items:
        assert isinstance(item, ResearchFindingOut)
        hits.append(
            AgentSearchHitOut(
                collection="findings",
                id=item.id,
                title=item.title,
                url=item.url,
                brand=item.brand,
            )
        )

    people = query_comment_people(q=needle, limit=per_collection)
    for item in people.items:
        assert isinstance(item, CommentPersonOut)
        hits.append(
            AgentSearchHitOut(
                collection="comment-people",
                id=item.id,
                title=item.display_name or item.handle,
                url=item.profile_url,
                brand=item.brand,
            )
        )

    threads = query_engagement_threads(q=needle, limit=per_collection)
    for item in threads.items:
        assert isinstance(item, EngagementThreadOut)
        hits.append(
            AgentSearchHitOut(
                collection="engagement-threads",
                id=item.id,
                title=item.title or item.url,
                url=item.url,
                brand=item.brand,
            )
        )

    return AgentSearchOut(q=q, hits=hits)
