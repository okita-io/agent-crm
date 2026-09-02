"""Rolling 1h / 4h / 24h catalog-growth deltas."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.contacts.growth import GROWTH_METRIC_KEYS, catalog_growth
from agent_crm.db import session_scope
from agent_crm.enums import Brand, ContactAudience, ContactEmailKind, HuntResourceKind
from agent_crm.models import Account, CommentPerson, ContactProfile, HuntResource


def _at(now: datetime, *, hours: float = 0) -> datetime:
    return now - timedelta(hours=hours)


def _add_profile(
    session,
    *,
    email: str,
    when: datetime,
    name: str | None = "Ada Lovelace",
    organization: str | None = "Analytical Engines",
    title: str | None = "Mathematician",
    socials: dict | None = None,
    with_socials: bool = True,
    brand: Brand = Brand.TACTIC_STUDIO,
    audience: ContactAudience = ContactAudience.MARKETING,
    email_kind: ContactEmailKind = ContactEmailKind.PERSON,
    enrichment: dict | None = None,
    updated_at: datetime | None = None,
) -> ContactProfile:
    if socials is None and with_socials:
        socials = {"linkedin": "https://linkedin.com/in/ada"}
    elif not with_socials:
        socials = None
    row = ContactProfile(
        email=email,
        name=name,
        organization=organization,
        title=title,
        socials=socials,
        brand=brand,
        audience=audience,
        email_kind=email_kind,
        enrichment=enrichment,
        source_urls=["https://example.com/team"],
    )
    session.add(row)
    session.flush()
    row.created_at = when
    row.updated_at = updated_at or when
    return row


def _add_resource(session, *, url: str, when: datetime, brand: Brand = Brand.TACTIC_STUDIO) -> None:
    row = HuntResource(
        url=url,
        domain="example.com",
        brand=brand,
        kind=HuntResourceKind.OTHER,
    )
    session.add(row)
    session.flush()
    row.created_at = when
    row.updated_at = when
    row.first_seen = when
    row.last_seen = when


def _add_commenter(session, *, handle: str, when: datetime, brand: Brand = Brand.MIDNIGHTSATIN) -> None:
    row = CommentPerson(
        platform="reddit",
        handle=handle,
        display_name=handle,
        brand=brand,
        audience=ContactAudience.END_USER,
        source_urls=["https://reddit.com/r/test"],
    )
    session.add(row)
    session.flush()
    row.created_at = when
    row.updated_at = when


def test_catalog_growth_windows_and_rates(db_url) -> None:
    now = datetime(2026, 9, 2, 18, 0, tzinfo=UTC)
    with session_scope() as session:
        _add_profile(session, email="fresh@studio.com", when=_at(now, hours=0.5))
        _add_profile(
            session,
            email="four@studio.com",
            when=_at(now, hours=2),
            organization="Four Corp",
        )
        _add_profile(
            session,
            email="day@studio.com",
            when=_at(now, hours=10),
            name=None,
            organization=None,
            title=None,
            with_socials=False,
        )
        _add_profile(
            session,
            email="old@studio.com",
            when=_at(now, hours=30),
            organization="Analytical Engines",
        )
        _add_profile(
            session,
            email="info@studio.com",
            when=_at(now, hours=0.25),
            name=None,
            organization=None,
            title=None,
            with_socials=False,
            email_kind=ContactEmailKind.ROLE,
        )
        _add_profile(
            session,
            email="twin@studio.com",
            when=_at(now, hours=0.75),
            organization="analytical engines",
        )
        _add_resource(session, url="https://fresh.example/page", when=_at(now, hours=0.5))
        _add_resource(session, url="https://day.example/page", when=_at(now, hours=8))
        _add_commenter(session, handle="freshfan", when=_at(now, hours=0.5))
        _add_commenter(session, handle="oldfan", when=_at(now, hours=30))
        account = Account(name="Nova Studio", website="https://novastudio.com")
        session.add(account)
        session.flush()
        account.created_at = _at(now, hours=0.5)
        account.updated_at = _at(now, hours=0.5)
        stale = _add_profile(
            session,
            email="enriched@studio.com",
            when=_at(now, hours=40),
            enrichment={"source": "people"},
            updated_at=_at(now, hours=0.5),
        )
        assert stale.created_at < now - timedelta(hours=24)

    report = catalog_growth(now=now)
    one = report["windows"]["1h"]
    four = report["windows"]["4h"]
    day = report["windows"]["24h"]

    assert one["emails"] == 3
    assert one["person_emails"] == 2
    assert one["names"] == 2
    assert one["companies"] == 1
    assert one["titles"] == 2
    assert one["socials"] == 2
    assert one["websites"] == 1
    assert one["commenters"] == 1
    assert one["accounts"] == 1
    assert one["enriched"] == 1

    assert four["emails"] == 4
    assert four["companies"] == 2
    assert day["emails"] == 5
    assert day["websites"] == 2
    assert day["commenters"] == 1

    assert report["per_hour"]["1h"]["emails"] == 3.0
    assert report["per_hour"]["4h"]["emails"] == 1.0
    assert report["per_hour"]["24h"]["emails"] == round(5 / 24, 2)
    assert set(one) == set(GROWTH_METRIC_KEYS)


def test_catalog_growth_brand_filter(db_url) -> None:
    now = datetime(2026, 9, 2, 18, 0, tzinfo=UTC)
    with session_scope() as session:
        _add_profile(
            session,
            email="tactic@studio.com",
            when=_at(now, hours=0.5),
            brand=Brand.TACTIC_STUDIO,
        )
        _add_profile(
            session,
            email="satin@books.com",
            when=_at(now, hours=0.5),
            brand=Brand.MIDNIGHTSATIN,
            audience=ContactAudience.END_USER,
            organization="Midnight Press",
        )
        _add_resource(
            session,
            url="https://tactic.example/page",
            when=_at(now, hours=0.5),
            brand=Brand.TACTIC_STUDIO,
        )
        _add_resource(
            session,
            url="https://satin.example/page",
            when=_at(now, hours=0.5),
            brand=Brand.MIDNIGHTSATIN,
        )

    filtered = catalog_growth(now=now, brand=Brand.TACTIC_STUDIO)
    assert filtered["windows"]["1h"]["emails"] == 1
    assert filtered["windows"]["1h"]["websites"] == 1
    assert filtered["windows"]["1h"]["accounts"] == 0


def test_catalog_growth_endpoint(db_url) -> None:
    now = datetime.now(UTC)
    with session_scope() as session:
        _add_profile(session, email="api@studio.com", when=_at(now, hours=0.5))

    client = TestClient(app)
    response = client.get("/report/growth")
    assert response.status_code == 200
    payload = response.json()
    assert "1h" in payload["windows"]
    assert "4h" in payload["windows"]
    assert "24h" in payload["windows"]
    assert payload["windows"]["1h"]["emails"] >= 1
    assert payload["per_hour"]["1h"]["emails"] >= 1
    tactic = client.get("/report/growth", params={"brand": Brand.TACTIC_STUDIO.value})
    assert tactic.status_code == 200
    assert tactic.json()["windows"]["1h"]["emails"] >= 1
