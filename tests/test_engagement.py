"""Tests for engagement scoring, venue cataloguing, and thread store."""

from __future__ import annotations

import json

import pytest

from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.engagement.runner import (
    extract_engagement_signals,
    is_engagement_venue,
    is_thread_url,
    venue_scan_queries,
)
from agent_crm.engagement.store import list_threads, upsert_draft, upsert_thread
from agent_crm.enums import Brand, HuntResourceKind
from agent_crm.hunt.feedback import HuntFeedbackBudget, enqueue_engagement_terms
from agent_crm.hunt.store import HuntStore
from agent_crm.hunt.utils import classify_resource_detailed, format_resource_notes


@pytest.fixture()
def eng_db(tmp_path, monkeypatch):
    db_path = tmp_path / "engagement.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield db_path
    reset_engine()
    get_settings.cache_clear()


def test_extract_engagement_signals_scores_member_and_hot_hints() -> None:
    signals = extract_engagement_signals(
        "Romance Books — 120,000 members",
        "Most popular weekly thread. 340 comments this week.",
        kind=HuntResourceKind.COMMUNITY,
    )
    assert signals.member_count == 120000
    assert signals.comment_count == 340
    assert signals.score >= 50
    assert "hot" in signals.signals or "most popular" in signals.signals


def test_is_thread_url_detects_reddit_and_discourse() -> None:
    assert is_thread_url("https://www.reddit.com/r/RomanceBooks/comments/abc123/title/")
    assert is_thread_url("https://forum.example/t/weekly-reads/42")
    assert not is_thread_url("https://www.reddit.com/r/RomanceBooks/")


def test_venue_scan_queries_for_reddit_sub() -> None:
    classification = classify_resource_detailed(
        "https://www.reddit.com/r/RomanceBooks/",
        "Romance Books",
    )
    terms = venue_scan_queries(classification, url="https://www.reddit.com/r/RomanceBooks/")
    assert any("site:reddit.com/r/RomanceBooks hot" in term for term in terms)
    assert any("top this week" in term for term in terms)
    assert is_engagement_venue(classification, "https://www.reddit.com/r/RomanceBooks/")
    assert not is_engagement_venue(
        classification,
        "https://www.reddit.com/r/RomanceBooks/comments/abc/title/",
    )


def test_upsert_resource_persists_engagement_score(eng_db) -> None:
    store = HuntStore()
    result = store.upsert_resource(
        url="https://www.reddit.com/r/RomanceBooks/",
        brand=Brand.MIDNIGHTSATIN,
        title="Romance Books — 50,000 members",
        found_via_query="romance forums",
        snippet="High traffic community. Most popular romance subreddit.",
    )
    assert result.resource is not None
    assert result.resource.engagement_score > 0
    notes = json.loads(result.resource.notes or "{}")
    assert notes.get("engagement", {}).get("score", 0) > 0
    assert notes.get("community") == "reddit"


def test_format_resource_notes_merges_engagement() -> None:
    classification = classify_resource_detailed(
        "https://www.reddit.com/r/RomanceBooks/",
        "Romance Books",
    )
    first = format_resource_notes(classification, "a quiet forum")
    merged = format_resource_notes(
        classification,
        "hot weekly thread",
        engagement={"score": 70, "signals": ["hot"]},
        existing=first,
    )
    payload = json.loads(merged or "{}")
    assert payload["community"] == "reddit"
    assert payload["engagement"]["score"] == 70
    assert payload["snippet"] == "hot weekly thread"


def test_enqueue_engagement_terms(eng_db) -> None:
    store = HuntStore()
    classification = classify_resource_detailed(
        "https://www.reddit.com/r/RomanceBooks/",
        "Romance Books",
    )
    budget = HuntFeedbackBudget(
        community_terms_remaining=0,
        person_terms_remaining=0,
        handle_terms_remaining=0,
        engagement_terms_remaining=10,
    )
    enqueued = enqueue_engagement_terms(
        store,
        classification=classification,
        url="https://www.reddit.com/r/RomanceBooks/",
        brand=Brand.MIDNIGHTSATIN,
        run_id="eng-test",
        budget=budget,
    )
    assert enqueued >= 2
    feedback = store.list_feedback_queries(brand=Brand.MIDNIGHTSATIN)
    origins = [row.origin for row in feedback]
    assert any(origin.startswith("engagement:") for origin in origins)


def test_upsert_thread_and_draft(eng_db) -> None:
    from agent_crm.engagement.runner import extract_engagement_signals

    signals = extract_engagement_signals(
        "Weekly recommendation megathread — 800 comments",
        "Hot post",
        kind=HuntResourceKind.FORUM,
    )
    thread = upsert_thread(
        url="https://www.reddit.com/r/RomanceBooks/comments/xyz/weekly/",
        brand=Brand.MIDNIGHTSATIN,
        title="Weekly recommendation megathread",
        signals=signals,
        platform="reddit",
        venue_url="https://reddit.com/r/RomanceBooks",
        excerpt="What are you reading this week?",
        found_via_query="site:reddit.com/r/RomanceBooks hot",
    )
    assert thread is not None
    assert thread.popularity_score > 0
    draft = upsert_draft(
        thread_id=thread.id,
        brand=Brand.MIDNIGHTSATIN,
        draft_text="If you like slow-burn serials, MidnightSatin has a similar weekly rec vibe.",
        product_angle="serial romance recs",
    )
    assert draft is not None
    rows = list_threads(brand=Brand.MIDNIGHTSATIN)
    assert len(rows) == 1
    assert rows[0].url.endswith("/comments/xyz/weekly")
