"""Tests for Hermes read-only /agent query API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.config import get_settings
from agent_crm.contacts.store import upsert_contact_profile
from agent_crm.db import init_db, reset_engine, session_scope
from agent_crm.enums import Brand, ContactAudience, HuntResourceKind, ResearchFindingKind
from agent_crm.hunt.store import HuntStore
from agent_crm.models import CommentPerson, ResearchFinding
from agent_crm.research.utils import canonical_url, extract_domain


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    db_path = tmp_path / "agent_query.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("CRM_API_TOKEN", "")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield TestClient(app)
    reset_engine()
    get_settings.cache_clear()


def _seed() -> None:
    upsert_contact_profile(
        email="jane.doe@novastudio.com",
        name="Jane Doe",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://novastudio.com/team",
        audience=ContactAudience.MARKETING,
    )
    HuntStore().upsert_resource(
        url="https://reddit.com/r/RomanceBooks",
        brand=Brand.MIDNIGHTSATIN,
        title="Romance Books community",
        found_via_query="romance communities",
        kind=HuntResourceKind.COMMUNITY,
    )
    with session_scope() as session:
        session.add(
            ResearchFinding(
                url=canonical_url("https://adboard.example/rate-card"),
                domain=extract_domain("https://adboard.example/rate-card"),
                title="Ad board rate card",
                brand=Brand.TACTIC_STUDIO,
                kind=ResearchFindingKind.AD_PLACEMENT,
                summary="Sells banner ads to XR studios",
                source_query="xr ad placement",
            )
        )
        session.add(
            CommentPerson(
                platform="reddit",
                handle="romancefan",
                display_name="Romance Fan",
                brand=Brand.MIDNIGHTSATIN,
                audience=ContactAudience.END_USER,
                source_urls=["https://reddit.com/r/RomanceBooks/comments/1"],
            )
        )


def test_agent_catalog(api_client) -> None:
    response = api_client.get("/agent/catalog")
    assert response.status_code == 200
    payload = response.json()
    assert "contacts" in payload["collections"]
    assert "websites" in payload["collections"]
    assert "engagement-threads" in payload["collections"]
    assert "seo-reviews" in payload["collections"]
    assert "seo-plans" in payload["collections"]
    assert "midnightsatin" in payload["brands"]


def test_agent_search_and_pages(api_client) -> None:
    _seed()
    search = api_client.get("/agent/search", params={"q": "romance"})
    assert search.status_code == 200
    hits = search.json()["hits"]
    collections = {hit["collection"] for hit in hits}
    assert "websites" in collections or "comment-people" in collections

    contacts = api_client.get("/agent/contacts", params={"q": "jane"})
    assert contacts.status_code == 200
    body = contacts.json()
    assert body["total"] >= 1
    assert body["offset"] == 0
    assert body["limit"] == 50
    assert body["items"][0]["email"] == "jane.doe@novastudio.com"

    websites = api_client.get("/agent/websites", params={"q": "RomanceBooks"})
    assert websites.status_code == 200
    assert websites.json()["total"] >= 1

    findings = api_client.get("/agent/findings", params={"brand": "tactic-studio"})
    assert findings.status_code == 200
    assert findings.json()["total"] >= 1

    people = api_client.get("/agent/comment-people", params={"q": "romancefan"})
    assert people.status_code == 200
    assert people.json()["total"] >= 1


def test_agent_routes_are_get_only(api_client) -> None:
    response = api_client.post("/agent/contacts", json={})
    assert response.status_code in {405, 404, 422}


def test_agent_search_treats_like_wildcards_as_literals(api_client) -> None:
    _seed()
    wild = api_client.get("/agent/contacts", params={"q": "%"})
    assert wild.status_code == 200
    assert wild.json()["total"] == 0
    exact = api_client.get("/agent/contacts", params={"q": "jane.doe"})
    assert exact.status_code == 200
    assert exact.json()["total"] >= 1


def test_agent_requires_token_when_configured(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "agent_auth.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("CRM_API_TOKEN", "hermes-secret")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    client = TestClient(app)
    assert client.get("/agent/catalog").status_code == 401
    ok = client.get("/agent/catalog", headers={"X-CRM-Token": "hermes-secret"})
    assert ok.status_code == 200
    reset_engine()
    get_settings.cache_clear()
