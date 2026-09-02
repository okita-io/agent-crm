"""Tests for hunt SERP topical relevance gating."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_crm.enums import Brand, TopicalRelevanceVerdict
from agent_crm.hunt.relevance import assess_topical_relevance, is_obvious_off_topic_url


@pytest.mark.parametrize(
    ("url", "brand"),
    [
        ("https://developer.mozilla.org/en-US/docs/Web/API", Brand.MIDNIGHTSATIN),
        ("https://www.mozilla.org/en-US/", Brand.TACTIC_STUDIO),
        ("https://www.w3.org/TR/webxr/", Brand.TACTIC_STUDIO),
    ],
)
def test_obvious_docs_domains_rejected(url: str, brand: Brand) -> None:
    assert is_obvious_off_topic_url(url) is not None
    result = assess_topical_relevance(
        brand=brand,
        url=url,
        title="Generic documentation",
        snippet="web platform reference",
        allow_spark=False,
    )
    assert result.verdict == TopicalRelevanceVerdict.OFF_TOPIC


def test_romance_article_accepted_for_midnightsatin() -> None:
    result = assess_topical_relevance(
        brand=Brand.MIDNIGHTSATIN,
        url="https://bookblog.example/best-dark-romance-booktok-communities",
        title="Best Dark Romance BookTok Communities for Readers",
        snippet="romance readers discuss spicy romance book clubs and romance book blogs",
        query="romance booktok communities",
        allow_spark=False,
    )
    assert result.verdict == TopicalRelevanceVerdict.ON_TOPIC


def test_ar_case_study_accepted_for_tactic_studio() -> None:
    result = assess_topical_relevance(
        brand=Brand.TACTIC_STUDIO,
        url="https://agency.example/webar-brand-activation-case-study",
        title="WebAR Brand Activation Case Study",
        snippet="augmented reality campaign using Snap AR lens for retail marketing",
        query="WebAR brand activation campaign",
        allow_spark=False,
    )
    assert result.verdict == TopicalRelevanceVerdict.ON_TOPIC


def test_retail_marketing_vp_page_accepted_for_tactic_studio() -> None:
    result = assess_topical_relevance(
        brand=Brand.TACTIC_STUDIO,
        url="https://grocery.example/leadership/jane-smith",
        title="Jane Smith, VP of Marketing at FreshMart Grocery",
        snippet=(
            "brand management and marketing manager leadership at a "
            "food and beverage retailer with $50 million revenue"
        ),
        query="VP of marketing grocery retail leadership team",
        allow_spark=False,
    )
    assert result.verdict == TopicalRelevanceVerdict.ON_TOPIC


def test_horoscope_page_accepted_for_celestial_nexus() -> None:
    result = assess_topical_relevance(
        brand=Brand.CELESTIAL_NEXUS,
        url="https://stars.example/daily-horoscope-natal-chart",
        title="Daily Horoscope and Natal Chart Guide",
        snippet="astrology community discusses zodiac signs and divination",
        query="horoscope natal chart community",
        allow_spark=False,
    )
    assert result.verdict == TopicalRelevanceVerdict.ON_TOPIC


def test_mens_interest_page_accepted_for_heybuddy() -> None:
    result = assess_topical_relevance(
        brand=Brand.HEYBUDDY,
        url="https://forum.example/mens-hobbies-and-lifestyle",
        title="Men's Interests Forum",
        snippet="discussion of men's hobbies, fitness, and lifestyle for guys",
        query="men's interests community",
        allow_spark=False,
    )
    assert result.verdict == TopicalRelevanceVerdict.ON_TOPIC


def test_query_cannot_mark_denied_host_on_topic() -> None:
    result = assess_topical_relevance(
        brand=Brand.MIDNIGHTSATIN,
        url="https://developer.mozilla.org/en-US/docs/Web/API",
        title="Generic documentation",
        snippet="web platform reference",
        query="romance booktok communities",
        allow_spark=False,
    )
    assert result.verdict == TopicalRelevanceVerdict.OFF_TOPIC


def test_docker_and_aggregator_hosts_are_denied() -> None:
    assert is_obvious_off_topic_url("https://hub.docker.com/r/library/nginx")
    assert is_obvious_off_topic_url("https://wiki.haskell.org/Web")
    assert is_obvious_off_topic_url("https://www.reuters.com/world/")
    assert is_obvious_off_topic_url("https://flipboard.com/topic/romance")


def test_spark_used_for_ambiguous_page() -> None:
    with patch("agent_crm.hunt.relevance.chat_completions") as mock_llm:
        mock_llm.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"verdict":"on_topic","reason":"AR retail case study"}'
                    }
                }
            ]
        }
        result = assess_topical_relevance(
            brand=Brand.TACTIC_STUDIO,
            url="https://retail.example/partners/showcase",
            title="Partner showcase",
            snippet="mixed retail technology stories",
            allow_spark=True,
        )
    assert result.verdict == TopicalRelevanceVerdict.ON_TOPIC
    assert result.spark_used is True
