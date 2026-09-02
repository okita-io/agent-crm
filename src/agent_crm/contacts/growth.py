"""Rolling catalog-growth deltas for 1h / 4h / 24h windows.

Counts rows first seen in each window (``created_at`` / ``first_seen``).
Per-hour rates make the windows comparable so a faster 1h pace versus the
24h baseline is a signal that a recent hunt or enrichment change is paying off.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, String, and_, cast, func, select

from agent_crm.db import session_scope
from agent_crm.enums import Brand, ContactAudience, ContactEmailKind
from agent_crm.models import Account, CommentPerson, ContactProfile, HuntResource

_JSON_EMPTY = ("null", "{}", "[]", "")

GROWTH_WINDOWS_HOURS: tuple[int, ...] = (1, 4, 24)

GROWTH_METRIC_KEYS: tuple[str, ...] = (
    "emails",
    "person_emails",
    "names",
    "companies",
    "titles",
    "socials",
    "websites",
    "commenters",
    "accounts",
    "enriched",
)


def _populated(column):
    return and_(column.isnot(None), func.trim(column) != "")


def _json_present(column):
    """True when a JSON column holds an object, not SQL/JSON null or {}."""
    text = func.lower(func.trim(cast(column, String)))
    return and_(column.isnot(None), text.notin_(_JSON_EMPTY))


def _filter_profiles(
    stmt: Select,
    *,
    brand: Brand | None,
    audience: ContactAudience | None,
) -> Select:
    if brand is not None:
        stmt = stmt.where(ContactProfile.brand == brand)
    if audience is not None:
        stmt = stmt.where(ContactProfile.audience == audience)
    return stmt


def _filter_comment_people(
    stmt: Select,
    *,
    brand: Brand | None,
    audience: ContactAudience | None,
) -> Select:
    if brand is not None:
        stmt = stmt.where(CommentPerson.brand == brand)
    if audience is not None:
        stmt = stmt.where(CommentPerson.audience == audience)
    return stmt


def _count(session, stmt: Select) -> int:
    return int(session.scalar(stmt) or 0)


def _window_counts(
    session,
    cutoff: datetime,
    *,
    brand: Brand | None,
    audience: ContactAudience | None,
) -> dict[str, int]:
    new_profiles = _filter_profiles(
        select(func.count()).select_from(ContactProfile).where(
            ContactProfile.created_at >= cutoff
        ),
        brand=brand,
        audience=audience,
    )
    person_emails = _filter_profiles(
        select(func.count()).select_from(ContactProfile).where(
            ContactProfile.created_at >= cutoff,
            ContactProfile.email_kind == ContactEmailKind.PERSON,
        ),
        brand=brand,
        audience=audience,
    )
    names = _filter_profiles(
        select(func.count()).select_from(ContactProfile).where(
            ContactProfile.created_at >= cutoff,
            _populated(ContactProfile.name),
        ),
        brand=brand,
        audience=audience,
    )
    companies = _filter_profiles(
        select(func.count(func.distinct(func.lower(ContactProfile.organization)))).where(
            ContactProfile.created_at >= cutoff,
            _populated(ContactProfile.organization),
        ),
        brand=brand,
        audience=audience,
    )
    titles = _filter_profiles(
        select(func.count()).select_from(ContactProfile).where(
            ContactProfile.created_at >= cutoff,
            _populated(ContactProfile.title),
        ),
        brand=brand,
        audience=audience,
    )
    socials = _filter_profiles(
        select(func.count()).select_from(ContactProfile).where(
            ContactProfile.created_at >= cutoff,
            _json_present(ContactProfile.socials),
        ),
        brand=brand,
        audience=audience,
    )
    websites = select(func.count()).select_from(HuntResource).where(
        HuntResource.first_seen >= cutoff
    )
    if brand is not None:
        websites = websites.where(HuntResource.brand == brand)

    commenters = _filter_comment_people(
        select(func.count()).select_from(CommentPerson).where(
            CommentPerson.created_at >= cutoff
        ),
        brand=brand,
        audience=audience,
    )
    enriched = _filter_profiles(
        select(func.count()).select_from(ContactProfile).where(
            ContactProfile.updated_at >= cutoff,
            _json_present(ContactProfile.enrichment),
        ),
        brand=brand,
        audience=audience,
    )

    accounts = 0
    if brand is None and audience is None:
        accounts = _count(
            session,
            select(func.count()).select_from(Account).where(
                Account.created_at >= cutoff
            ),
        )

    return {
        "emails": _count(session, new_profiles),
        "person_emails": _count(session, person_emails),
        "names": _count(session, names),
        "companies": _count(session, companies),
        "titles": _count(session, titles),
        "socials": _count(session, socials),
        "websites": _count(session, websites),
        "commenters": _count(session, commenters),
        "accounts": accounts,
        "enriched": _count(session, enriched),
    }


def catalog_growth(
    *,
    now: datetime | None = None,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
) -> dict[str, Any]:
    """Return new-row counts and per-hour rates for 1h, 4h, and 24h windows."""
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)

    with session_scope() as session:
        windows = {
            f"{hours}h": _window_counts(
                session,
                reference - timedelta(hours=hours),
                brand=brand,
                audience=audience,
            )
            for hours in GROWTH_WINDOWS_HOURS
        }

    per_hour = {
        f"{hours}h": {
            metric: round(windows[f"{hours}h"][metric] / hours, 2)
            for metric in GROWTH_METRIC_KEYS
        }
        for hours in GROWTH_WINDOWS_HOURS
    }
    return {
        "generated_at": reference,
        "windows": windows,
        "per_hour": per_hour,
    }
