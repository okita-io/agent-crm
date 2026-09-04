"""Tests for vendored marketing-agi skill loading."""

from __future__ import annotations

from unittest.mock import patch

import httpx

from agent_crm.enums import Brand, ResearchFindingKind
from agent_crm.marketing_skill import (
    competitor_summarizer_guidance,
    marketing_skill_root,
    skill_file_exists,
)
from agent_crm.research.runner import _maybe_summarize
from agent_crm.firecrawl_client import ScrapeResult
from agent_crm.searxng_client import SearchResult


def test_marketing_skill_files_exist() -> None:
    root = marketing_skill_root()
    assert root.is_dir()
    assert (root / "SKILL.md").is_file()
    assert (root / "LICENSE").is_file()
    assert (root / "SOURCE").is_file()
    assert skill_file_exists("references/competitive.md")
    assert skill_file_exists("references/positioning.md")
    assert skill_file_exists("references/paid-ads.md")
    assert skill_file_exists("references/hooks.md")
    # Progressive disclosure: fourteen reference modules, not one concatenated file.
    reference_files = list((root / "references").glob("*.md"))
    assert len(reference_files) >= 14


def test_competitor_summarizer_guidance_includes_competitive_module() -> None:
    guidance = competitor_summarizer_guidance()
    assert "competitive teardown" in guidance.lower()
    assert "positioning read" in guidance.lower()
    assert "public sources only" in guidance.lower()


def test_maybe_summarize_competitor_includes_competitive_guidance(db_url) -> None:
    hit = SearchResult(
        url="https://co-star.app",
        title="Co–Star Astrology",
        snippet="Natal chart app",
    )
    page = ScrapeResult(
        url=hit.url,
        title="Co-Star",
        markdown="Astrology app content.",
        metadata={},
    )
    errors: list[str] = []

    with patch("agent_crm.research.runner.chat_completions") as mock_llm:
        mock_llm.return_value = {
            "choices": [{"message": {"content": '{"summary": "Competitor note."}'}}]
        }
        _maybe_summarize(
            hit,
            page,
            "fallback",
            brand=Brand.CELESTIAL_NEXUS,
            kind=ResearchFindingKind.COMPETITOR,
            errors=errors,
        )
        assert mock_llm.called
        messages = mock_llm.call_args[0][0]["messages"]
        system = messages[0]["content"]
        assert "competitive teardown" in system.lower()
        assert "competitive.md" in system.lower()
