"""Tests for hunt query priority dequeue ordering."""

from __future__ import annotations

import pytest

from agent_crm.db import init_db, reset_engine
from agent_crm.enums import Brand, ContactAudience
from agent_crm.hunt.priority import hunt_query_priority
from agent_crm.hunt.store import HuntStore


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    db_path = tmp_path / "hunt_priority.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    from agent_crm.config import get_settings

    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield f"sqlite:///{db_path}"
    reset_engine()
    get_settings.cache_clear()


def test_hunt_query_priority_ordering() -> None:
    assert hunt_query_priority(Brand.TACTIC_STUDIO, ContactAudience.MARKETING) == 100
    assert hunt_query_priority(Brand.TACTIC_STUDIO, ContactAudience.INFLUENCER) == 90
    assert hunt_query_priority(Brand.TACTIC_STUDIO, ContactAudience.USER) == 80
    assert hunt_query_priority(Brand.MIDNIGHTSATIN, ContactAudience.INFLUENCER) == 70
    assert hunt_query_priority(Brand.MIDNIGHTSATIN, ContactAudience.USER) == 65
    assert hunt_query_priority(Brand.MIDNIGHTSATIN, None) == 30


def test_tactic_marketing_dequeues_before_older_midnightsatin(db_url) -> None:
    store = HuntStore()
    store.enqueue_query(
        query="older ms seed",
        brand=Brand.MIDNIGHTSATIN,
        origin="seed_pack",
    )
    store.enqueue_query(
        query="tactic marketing lead",
        brand=Brand.TACTIC_STUDIO,
        origin="marketing:seed_pack",
    )
    pending = store.next_pending_query()
    assert pending is not None
    assert pending.brand == Brand.TACTIC_STUDIO
    assert pending.origin.startswith("marketing:")


def test_within_tactic_marketing_before_influencer_before_user(db_url) -> None:
    store = HuntStore()
    store.enqueue_query(
        query="tactic user community",
        brand=Brand.TACTIC_STUDIO,
        origin="user:seed_pack",
    )
    store.enqueue_query(
        query="tactic influencer creator",
        brand=Brand.TACTIC_STUDIO,
        origin="influencer:seed_pack",
    )
    store.enqueue_query(
        query="tactic marketing brand",
        brand=Brand.TACTIC_STUDIO,
        origin="marketing:seed_pack",
    )
    first = store.next_pending_query(brand=Brand.TACTIC_STUDIO)
    assert first is not None
    assert first.origin.startswith("marketing:")
    store.mark_query_completed(first.id)
    second = store.next_pending_query(brand=Brand.TACTIC_STUDIO)
    assert second is not None
    assert second.origin.startswith("influencer:")
    store.mark_query_completed(second.id)
    third = store.next_pending_query(brand=Brand.TACTIC_STUDIO)
    assert third is not None
    assert third.origin.startswith("user:")


def test_branded_loop_filters_but_respects_priority(db_url) -> None:
    store = HuntStore()
    store.enqueue_query(
        query="ms generic",
        brand=Brand.MIDNIGHTSATIN,
        origin="seed_pack",
    )
    store.enqueue_query(
        query="tactic user",
        brand=Brand.TACTIC_STUDIO,
        origin="user:seed_pack",
    )
    branded = store.next_pending_query(brand=Brand.TACTIC_STUDIO)
    assert branded is not None
    assert branded.brand == Brand.TACTIC_STUDIO


def test_global_loop_has_no_brand_filter(db_url) -> None:
    store = HuntStore()
    store.enqueue_query(
        query="ms row",
        brand=Brand.MIDNIGHTSATIN,
        origin="seed_pack",
    )
    store.enqueue_query(
        query="tactic marketing",
        brand=Brand.TACTIC_STUDIO,
        origin="marketing:seed_pack",
    )
    global_next = store.next_pending_query()
    assert global_next is not None
    assert global_next.brand == Brand.TACTIC_STUDIO
