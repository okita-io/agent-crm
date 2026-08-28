"""Tests for hunt URL/query utilities."""

from __future__ import annotations

from agent_crm.enums import ContactAudience, HuntResourceKind
from agent_crm.hunt_feedback import (
    community_search_terms,
    is_valid_hunt_person_name,
    person_search_terms,
)
from agent_crm.hunt_utils import (
    canonical_url,
    classify_resource,
    classify_resource_detailed,
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


def test_reddit_subreddit_classified_as_community():
    classification = classify_resource_detailed(
        "https://www.reddit.com/r/RomanceBooks/",
        "Romance Books subreddit",
    )
    assert classification.kind == HuntResourceKind.COMMUNITY
    assert classification.platform == "reddit"
    assert classification.community_slug == "RomanceBooks"


def test_classify_resource_reddit_sub_not_social():
    kind = classify_resource(
        "https://reddit.com/r/RomanceBooks",
        "Romance Books",
    )
    assert kind == HuntResourceKind.COMMUNITY


def test_community_search_terms_for_reddit():
    classification = classify_resource_detailed(
        "https://reddit.com/r/RomanceBooks",
        "Romance Books",
    )
    terms = community_search_terms(classification, title="Romance Books")
    assert any("site:reddit.com/r/RomanceBooks" in term for term in terms)
    assert any("RomanceBooks discord" in term for term in terms)
    assert 2 <= len(terms) <= 4


def test_person_search_terms_and_name_validation():
    assert is_valid_hunt_person_name("Ada Vega")
    assert not is_valid_hunt_person_name("Admin")
    assert not is_valid_hunt_person_name("noreply")
    terms = person_search_terms("Ada Vega")
    assert len(terms) == 4
    assert all("Ada Vega" in term for term in terms)
    assert all("@" not in term for term in terms)


def test_marketing_person_search_terms_target_brand_leadership():
    terms = person_search_terms("Ada Vega", audience=ContactAudience.MARKETING)
    combined = " ".join(terms).lower()
    assert len(terms) == 4
    assert all("Ada Vega" in term for term in terms)
    assert "vp of marketing" in combined
    assert "brand manager" in combined
    assert "food and beverage" in combined
    assert all("@" not in term for term in terms)
