"""Tests for mission/focus prompt wiring into hunt and enrichment loops."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_crm.enums import Brand, ContactAudience
from agent_crm.hunt.loop import _llm_branch_terms
from agent_crm.projects.mission import log_mission_focus, supplemental_seed_queries


def test_supplemental_seed_queries_parses_query_lines(tmp_path, monkeypatch) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "tactic-studio.yaml").write_text(
        """
slug: tactic-studio
name: tactic.studio
status: live
enabled: true
channels:
  hunter:
    armed: true
    prompt: |
      Focus museums.
      query: IMLS museum interactive exhibit grant awardee
      seed: NEH digital humanities immersive grant recipient
  research:
    armed: true
    prompt: |
      query: Grants.gov museum AR award recipient
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CRM_PROJECTS_DIR", str(projects_dir))
    from agent_crm.config import get_settings
    from agent_crm.projects.channel_flags import clear_channel_cache

    get_settings.cache_clear()
    clear_channel_cache()

    hunter_queries = supplemental_seed_queries(Brand.TACTIC_STUDIO, "hunter")
    assert "IMLS museum interactive exhibit grant awardee" in hunter_queries
    assert "NEH digital humanities immersive grant recipient" in hunter_queries

    research_queries = supplemental_seed_queries(Brand.TACTIC_STUDIO, "research")
    assert research_queries == ["Grants.gov museum AR award recipient"]


def test_log_mission_focus_reads_project_yaml(tmp_path, monkeypatch, caplog) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "tactic-studio.yaml").write_text(
        """
slug: tactic-studio
name: tactic.studio
status: live
enabled: true
origin_prompt: |
  Museum grant awardee institutions.
channels:
  hunter:
    armed: true
    prompt: |
      Hunt exhibit leadership at IMLS awardees.
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CRM_PROJECTS_DIR", str(projects_dir))
    from agent_crm.config import get_settings
    from agent_crm.projects.channel_flags import clear_channel_cache

    get_settings.cache_clear()
    clear_channel_cache()

    with caplog.at_level("INFO"):
        focus = log_mission_focus("outbound_hunter", Brand.TACTIC_STUDIO, "hunter")

    assert "Museum grant awardee" in focus
    assert "IMLS awardees" in focus
    assert any("mission focus" in record.message for record in caplog.records)


def test_branch_terms_injects_mission_focus(tmp_path, monkeypatch) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "tactic-studio.yaml").write_text(
        """
slug: tactic-studio
name: tactic.studio
status: live
enabled: true
origin_prompt: |
  Find museum grant awardees for interactive exhibits.
channels:
  hunter:
    armed: true
    prompt: |
      Priority: exhibits and digital media contacts at grant-funded museums.
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CRM_PROJECTS_DIR", str(projects_dir))
    from agent_crm.config import get_settings
    from agent_crm.projects.channel_flags import clear_channel_cache

    get_settings.cache_clear()
    clear_channel_cache()

    with patch("agent_crm.hunt.loop.chat_completions") as mock_llm:
        mock_llm.return_value = {"choices": [{"message": {"content": '{"terms": []}'}}]}
        _llm_branch_terms(
            "IMLS museum grant",
            [
                {
                    "title": "Award",
                    "url": "https://museum.example/grant",
                    "content": "interactive exhibit grant",
                }
            ],
            max_terms=3,
            brand=Brand.TACTIC_STUDIO,
            audience=ContactAudience.MARKETING,
        )

    prompt = mock_llm.call_args[0][0]["messages"][1]["content"].lower()
    assert "mission focus" in prompt
    assert "museum grant awardee" in prompt
    assert "grant-funded museums" in prompt
    assert "food" not in prompt and "beverage" not in prompt
