"""Tests for live hunt-loop status helpers and API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.config import get_settings
from agent_crm.contact_store import upsert_contact_profile
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import Brand, ContactAudience, HuntQueryStatus
from agent_crm.hunt_status import (
    STALE_RUNNING_MINUTES,
    build_hunt_status,
    infer_hunt_phase,
    is_fresh_running,
)
from agent_crm.hunt_store import HuntStore
from agent_crm.models import HuntQuery
from agent_crm.db import session_scope
from sqlalchemy import select


def test_is_fresh_running_filters_stale_rows():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    fresh = now - timedelta(minutes=STALE_RUNNING_MINUTES - 1)
    stale = now - timedelta(minutes=STALE_RUNNING_MINUTES + 1)
    assert is_fresh_running(fresh, now=now)
    assert not is_fresh_running(stale, now=now)


@pytest.mark.parametrize(
    ("has_fresh_running", "waiting", "in_flight", "expected"),
    [
        (False, 0, 0, "idle / queue empty"),
        (True, 0, 0, "Searching / scraping (GPU idle until this query finishes)"),
        (False, 1, 0, "LLM / Spark"),
        (True, 0, 2, "LLM / Spark"),
    ],
)
def test_infer_hunt_phase(has_fresh_running, waiting, in_flight, expected):
    assert (
        infer_hunt_phase(
            has_fresh_running=has_fresh_running,
            spark_waiting=waiting,
            spark_in_flight=in_flight,
        )
        == expected
    )


def test_current_running_query_ignores_stale_running(db_url):
    store = HuntStore()
    store.enqueue_query(
        query="fresh query",
        brand=Brand.TACTIC_STUDIO,
        origin="marketing:seed",
        priority=100,
    )
    store.enqueue_query(
        query="stale query",
        brand=Brand.MIDNIGHTSATIN,
        origin="seed",
        priority=30,
    )

    with session_scope() as session:
        fresh = session.scalar(select(HuntQuery).where(HuntQuery.query == "fresh query"))
        stale = session.scalar(select(HuntQuery).where(HuntQuery.query == "stale query"))
        assert fresh is not None
        assert stale is not None
        fresh.status = HuntQueryStatus.RUNNING
        stale.status = HuntQueryStatus.RUNNING
        fresh.updated_at = datetime.now(UTC)
        stale.updated_at = datetime.now(UTC) - timedelta(minutes=STALE_RUNNING_MINUTES + 5)

    current = store.current_running_query()
    assert current is not None
    assert current.query == "fresh query"


def test_queue_breakdown_groups_by_brand_priority_status(db_url):
    store = HuntStore()
    store.enqueue_query(
        query="marketing one",
        brand=Brand.TACTIC_STUDIO,
        origin="marketing:seed",
        priority=100,
    )
    store.enqueue_query(
        query="marketing two",
        brand=Brand.TACTIC_STUDIO,
        origin="marketing:seed",
        priority=100,
    )
    store.enqueue_query(
        query="midnight seed",
        brand=Brand.MIDNIGHTSATIN,
        origin="seed",
        priority=30,
    )

    with session_scope() as session:
        row = session.scalar(select(HuntQuery).where(HuntQuery.query == "midnight seed"))
        assert row is not None
        row.status = HuntQueryStatus.COMPLETED
        row.completed_at = datetime.now(UTC)

    breakdown = store.queue_breakdown()
    pending_marketing = next(
        row
        for row in breakdown
        if row["brand"] == Brand.TACTIC_STUDIO.value
        and row["priority"] == 100
        and row["status"] == HuntQueryStatus.PENDING.value
    )
    completed_midnight = next(
        row
        for row in breakdown
        if row["brand"] == Brand.MIDNIGHTSATIN.value
        and row["priority"] == 30
        and row["status"] == HuntQueryStatus.COMPLETED.value
    )
    assert pending_marketing["count"] == 2
    assert completed_midnight["count"] == 1


def test_build_hunt_status_includes_email_counts_and_completed(db_url):
    store = HuntStore()
    store.enqueue_query(
        query="done query",
        brand=Brand.TACTIC_STUDIO,
        origin="marketing:seed",
        priority=100,
    )
    with session_scope() as session:
        row = session.scalar(select(HuntQuery).where(HuntQuery.query == "done query"))
        assert row is not None
        row.status = HuntQueryStatus.COMPLETED
        row.completed_at = datetime.now(UTC)

    upsert_contact_profile(
        email="pete.smith@tactic.studio",
        name="Pete Smith",
        brand=Brand.TACTIC_STUDIO,
        source_url="https://tactic.studio/team",
        audience=ContactAudience.MARKETING,
    )
    upsert_contact_profile(
        email="jane.doe@tactic.studio",
        name="Jane Doe",
        brand=Brand.TACTIC_STUDIO,
        source_url="https://tactic.studio/creator",
        audience=ContactAudience.INFLUENCER,
    )
    upsert_contact_profile(
        email="info@tactic.studio",
        name=None,
        brand=Brand.TACTIC_STUDIO,
        source_url="https://tactic.studio/contact",
        audience=ContactAudience.MARKETING,
    )

    status = build_hunt_status(store=store, queue_health=None, now=datetime.now(UTC))
    assert status["phase"] == "idle / queue empty"
    assert status["tactic_studio_email_total"] == 2
    assert status["tactic_studio_person_email_total"] == 2
    assert status["tactic_studio_all_email_total"] == 3
    assert len(status["recently_completed"]) == 1
    assert status["recently_completed"][0]["query"] == "done query"
    assert any(
        row["brand"] == Brand.TACTIC_STUDIO.value and row["count"] == 1
        for row in status["email_counts"]
    )


def test_hunt_status_endpoint(tmp_path, monkeypatch):
    db_path = tmp_path / "api-hunt-status.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()

    store = HuntStore()
    store.enqueue_query(
        query="running now",
        brand=Brand.TACTIC_STUDIO,
        origin="marketing:seed",
        priority=100,
    )
    with session_scope() as session:
        row = session.scalar(select(HuntQuery).where(HuntQuery.query == "running now"))
        assert row is not None
        row.status = HuntQueryStatus.RUNNING
        row.updated_at = datetime.now(UTC)

    client = TestClient(app)
    response = client.get("/hunt/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["phase"] == "Searching / scraping (GPU idle until this query finishes)"
    assert payload["now_playing"]["query"] == "running now"
    assert payload["now_playing"]["audience"] == "marketing"
    assert payload["tactic_studio_email_goal"] == 100
    reset_engine()
    get_settings.cache_clear()
