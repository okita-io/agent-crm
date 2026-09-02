"""Tests for search-queue keep/toss review."""

from __future__ import annotations

import pytest

from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import Brand, HuntQueryStatus, ResearchFindingKind
from agent_crm.hunt.store import HuntStore
from agent_crm.hunt.utils import origin_needs_review, query_enqueue_status
from agent_crm.queue_review import QueueReviewBudget, assess_search_query, run_queue_review
from agent_crm.research.query_store import ResearchQueryStore


@pytest.fixture()
def review_db(tmp_path, monkeypatch):
    db_path = tmp_path / "queue-review.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield db_path
    reset_engine()
    get_settings.cache_clear()


def test_origin_needs_review_skips_trusted_seeds() -> None:
    assert not origin_needs_review("seed")
    assert not origin_needs_review("seed_pack")
    assert not origin_needs_review("marketing:seed")
    assert not origin_needs_review("marketing:seed_pack")
    assert not origin_needs_review("venue:reddit.com/r/RomanceBooks")
    assert origin_needs_review("branch:seed")
    assert origin_needs_review("community:reddit/RomanceBooks")
    assert origin_needs_review("marketing:community:reddit/RomanceBooks")
    assert not origin_needs_review("treg:paid:hunter:treg.people.enrich")
    assert not origin_needs_review("treg:free:research:treg.google.serp.organic")
    assert query_enqueue_status("treg:paid:research:treg.google.serp.organic") == HuntQueryStatus.PENDING


def test_assess_search_query_tosses_noise_and_keeps_romance() -> None:
    tossed = assess_search_query(
        brand=Brand.MIDNIGHTSATIN,
        query="hub.docker.com nginx image",
        origin="branch:seed",
        allow_spark=False,
    )
    assert tossed.keep is False
    kept = assess_search_query(
        brand=Brand.MIDNIGHTSATIN,
        query="best dark romance booktok communities",
        origin="branch:seed",
        allow_spark=False,
    )
    assert kept.keep is True
    trusted = assess_search_query(
        brand=Brand.MIDNIGHTSATIN,
        query="generic discovery term xyz",
        origin="seed_pack",
        allow_spark=False,
    )
    assert trusted.keep is True


def test_enqueue_branch_waits_for_review(review_db) -> None:
    store = HuntStore()
    assert store.enqueue_query(
        query="mozilla mdn romance docs",
        brand=Brand.MIDNIGHTSATIN,
        origin="branch:seed",
    )
    assert store.claim_next_pending_query(brand=Brand.MIDNIGHTSATIN) is None
    pending_review = store.claim_next_pending_review_query()
    assert pending_review is not None
    query_id, _brand, query, origin = pending_review
    assert query_id > 0
    assert "mozilla" in query
    assert origin.startswith("branch:")


def test_queue_review_keeps_and_tosses(review_db) -> None:
    hunt = HuntStore()
    hunt.enqueue_query(
        query="romance booktok reader discord",
        brand=Brand.MIDNIGHTSATIN,
        origin="branch:seed",
    )
    hunt.enqueue_query(
        query="haskell.org wiki language",
        brand=Brand.MIDNIGHTSATIN,
        origin="branch:seed",
    )
    research = ResearchQueryStore()
    research.enqueue_query(
        query="docker hub astrology image",
        brand=Brand.CELESTIAL_NEXUS,
        kind=ResearchFindingKind.COMPETITOR,
        origin="branch:test",
    )
    result = run_queue_review(
        budget=QueueReviewBudget(max_queries=10, max_minutes=1, allow_spark=False)
    )
    assert result.reviewed == 3
    assert result.kept == 1
    assert result.tossed == 2
    kept = hunt.claim_next_pending_query(brand=Brand.MIDNIGHTSATIN)
    assert kept is not None
    assert "romance" in kept.query
    assert hunt.claim_next_pending_query(brand=Brand.MIDNIGHTSATIN) is None
    assert research.claim_next_pending_query() is None
    status = research.queue_status()
    assert status.get("rejected", 0) == 1