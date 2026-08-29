"""Tests for the append-only research query store."""

from __future__ import annotations

import pytest

from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import Brand, ResearchFindingKind, ResearchQueryStatus
from agent_crm.research_query_store import ResearchQueryStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    db_path = tmp_path / "research-query-store.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield ResearchQueryStore()
    reset_engine()
    get_settings.cache_clear()


def test_enqueue_is_idempotent_and_never_deletes(store: ResearchQueryStore) -> None:
    assert store.enqueue_query(
        query="natal chart app",
        brand=Brand.CELESTIAL_NEXUS,
        kind=ResearchFindingKind.COMPETITOR,
        origin="seed_pack",
    )
    assert not store.enqueue_query(
        query="Natal Chart App",
        brand=Brand.CELESTIAL_NEXUS,
        kind=ResearchFindingKind.COMPETITOR,
        origin="seed_pack",
    )
    assert store.count_all() == 1
    assert not hasattr(store, "delete_query")
    assert not hasattr(store, "dequeue_query")


def test_completing_a_query_does_not_shrink_the_queue(store: ResearchQueryStore) -> None:
    store.enqueue_query(
        query="seed",
        brand=Brand.MIDNIGHTSATIN,
        kind=ResearchFindingKind.COMPETITOR,
    )
    store.enqueue_query(
        query="follow-up galatea romance app",
        brand=Brand.MIDNIGHTSATIN,
        kind=ResearchFindingKind.COMPETITOR,
        origin="branch:seed",
    )
    before = store.count_all()
    claimed = store.claim_next_pending_query(brand=Brand.MIDNIGHTSATIN)
    assert claimed is not None
    store.mark_query_completed(claimed.id)
    assert store.count_all() == before
    assert store.count_pending(brand=Brand.MIDNIGHTSATIN) == 0
    assert store.queue_status().get("pending_review", 0) == 1
    row = store.get_by_dedupe(
        Brand.MIDNIGHTSATIN, ResearchFindingKind.COMPETITOR, claimed.query
    )
    assert row is not None
    assert row.status == ResearchQueryStatus.COMPLETED
