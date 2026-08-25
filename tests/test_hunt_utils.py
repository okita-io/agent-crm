"""Tests for hunt URL/query utilities."""

from __future__ import annotations

from agent_crm.hunt_utils import (
    canonical_url,
    extract_heuristic_terms,
    is_junk_title,
    is_junk_url,
    make_dedupe_key,
    normalize_query,
)


def test_normalize_query_dedupes_whitespace_and_case():
    assert normalize_query("  BookTok   Communities ") == "booktok communities"


def test_make_dedupe_key_includes_params():
    key_a = make_dedupe_key("foo", {"categories": "news"})
    key_b = make_dedupe_key("foo", {"categories": "general"})
    assert key_a != key_b


def test_canonical_url_strips_tracking():
    raw = "https://www.Example.com/path/?utm_source=x&b=2"
    assert canonical_url(raw) == "https://example.com/path?b=2"


def test_is_junk_title_filters_interstitials():
    assert is_junk_title("Just a moment...")
    assert is_junk_title("Home")
    assert not is_junk_title("Best romance book blogs")


def test_is_junk_url_filters_login_walls():
    assert is_junk_url("https://site.example/login")
    assert not is_junk_url("https://site.example/community")


def test_extract_heuristic_terms_caps_and_dedupes():
    results = [
        {
            "title": "10 Best BookTok Communities for Readers",
            "url": "https://example.com/list",
            "content": "reddit.com/r/romancebooks is popular",
        },
        {
            "title": "10 Best BookTok Communities for Readers",
            "url": "https://example.com/list2",
            "content": "",
        },
    ]
    terms = extract_heuristic_terms(results, max_terms=3)
    assert len(terms) >= 1
    assert len(terms) <= 3
