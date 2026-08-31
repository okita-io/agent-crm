"""Tests for the AEO/GEO extractability engine and skill loader."""

from __future__ import annotations

from agent_crm.aeo_geo import (
    detect_aeo_geo_issues,
    extract_extractability_signals,
    pick_one_aeo_geo_thing,
    score_aeo_geo_issues,
)
from agent_crm.aeo_geo_skill import aeo_geo_skill_root, review_writer_guidance, skill_file_exists


def test_aeo_geo_skill_files_exist() -> None:
    root = aeo_geo_skill_root()
    assert root.is_dir()
    assert (root / "SKILL.md").is_file()
    assert (root / "SOURCE").is_file()
    assert skill_file_exists("references/aeo-geo-review.md")
    assert skill_file_exists("references/aeo-geo-plan.md")


def test_review_guidance_mentions_aeo_and_geo() -> None:
    guidance = review_writer_guidance()
    lowered = guidance.lower()
    assert "aeo" in lowered
    assert "geo" in lowered
    assert "never invent" in lowered or "[need:" in lowered
    assert "tactic.studio" in lowered or "outreach" in lowered


def test_extractability_signals_and_issues() -> None:
    markdown = (
        "# Industrial AR training\n\n"
        "We build modules for plants.\n\n"
        "## What is WebAR training for manufacturing?\n\n"
        "Generic marketing copy without numbers. " * 8 + "\n\n"
        "## FAQ\n\n"
        "### How long does setup take?\n\n"
        "About two weeks for a pilot.\n"
    )
    signals = extract_extractability_signals(
        "https://tactic.studio/",
        markdown=markdown,
        metadata={"title": "Industrial AR training | tactic.studio"},
        title="Industrial AR training | tactic.studio",
    )
    assert signals.has_faq_section is True
    assert len(signals.question_headings) >= 1
    issues = {item.issue_id: item for item in detect_aeo_geo_issues(signals)}
    assert "no-quotable-evidence" in issues or "buried-answer" in issues or "thin-extractable-content" in issues
    score = score_aeo_geo_issues(list(issues.values()))
    assert 0 <= score <= 100
    one = pick_one_aeo_geo_thing(list(issues.values()), owned=True)
    assert one


def test_healthy_quotable_page_fewer_issues() -> None:
    markdown = (
        "# WebAR industrial training\n\n"
        "tactic.studio is a WebAR vendor for plant teams. "
        "In a 2024 pilot, 12 plants cut training time by 18% (internal benchmark).\n\n"
        "## What is industrial AR training?\n\n"
        "Industrial AR training overlays work instructions on real equipment. "
        "Technicians follow steps without paper binders. " * 10 + "\n\n"
        "## FAQ\n\n"
        "### How does WebAR compare to native AR apps?\n\n"
        "WebAR runs in the mobile browser; native apps need an app store install.\n\n"
        "| Approach | Install required |\n"
        "| --- | --- |\n"
        "| WebAR | No |\n"
        "| Native AR | Yes |\n"
    )
    metadata = {
        "title": "Industrial AR training",
        "jsonld": {"@type": "Organization", "sameAs": ["https://www.linkedin.com/company/tactic"]},
    }
    signals = extract_extractability_signals(
        "https://tactic.studio/training",
        markdown=markdown,
        metadata=metadata,
        title="Industrial AR training",
    )
    issues = detect_aeo_geo_issues(signals)
    ids = {item.issue_id for item in issues}
    assert "thin-extractable-content" not in ids
    assert "missing-visible-faq" not in ids
    assert signals.statistics_count >= 1
