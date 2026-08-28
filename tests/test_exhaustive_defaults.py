"""Tests for exhaustive search/scrape defaults."""

from __future__ import annotations

from agent_crm.config import Settings
from agent_crm.schemas import HuntLoopRequest, HuntRequest, ResearchRequest


def test_hunter_defaults_are_exhaustive() -> None:
    settings = Settings()
    assert settings.hunter_search_result_limit == 50
    assert settings.hunter_max_pages_per_run == 50
    assert settings.hunter_max_queries_default == 0
    assert settings.hunter_max_minutes_default == 0

    hunt = HuntRequest(query="studio")
    assert hunt.max_pages == 50
    assert hunt.search_limit == 50

    loop = HuntLoopRequest()
    assert loop.max_queries == 0
    assert loop.max_minutes == 0

    assert HuntLoopRequest(max_queries=40).max_queries == 40
    assert HuntLoopRequest(max_queries=None).max_queries == 0

    assert HuntLoopRequest(max_minutes=60).max_minutes == 60
    assert HuntLoopRequest(max_minutes=None).max_minutes == 0


def test_engagement_defaults() -> None:
    settings = Settings()
    assert settings.hunter_engagement_terms_per_run == 20
    assert settings.engagement_max_venues_per_run == 10
    assert settings.engagement_max_pages_per_venue == 15
    assert settings.engagement_max_minutes_default == 45
    assert settings.engagement_draft_threshold == 55


def test_research_defaults_are_exhaustive() -> None:
    settings = Settings()
    assert settings.research_search_result_limit == 50
    assert settings.research_max_pages_per_run == 200
    assert settings.research_max_queries_default == 20
    assert settings.research_max_minutes_default == 60

    from agent_crm.enums import Brand

    research = ResearchRequest(brand=Brand.HEYBUDDY)
    assert research.max_pages == 200
    assert research.search_limit == 50
    assert research.max_queries == 20
    assert research.max_minutes == 60
