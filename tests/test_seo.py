"""Tests for the SEO page-signal extractor and issue engine."""

from __future__ import annotations

from agent_crm.seo.runner import (
    detect_issues,
    extract_page_signals,
    pick_one_thing,
    related_paths_to_fetch,
    score_issues,
)
from agent_crm.seo.skill import open_seo_skill_root, skill_file_exists


def test_open_seo_skill_files_exist() -> None:
    root = open_seo_skill_root()
    assert root.is_dir()
    assert (root / "SKILL.md").is_file()
    assert (root / "SOURCE").is_file()
    assert skill_file_exists("references/site-audit.md")
    assert skill_file_exists("references/seo-plan.md")


def test_extract_signals_and_core_issues() -> None:
    markdown = (
        "Welcome to the app.\n\n"
        "![hero](https://midnightsatin.app/hero.png)\n"
        "![](https://midnightsatin.app/icon.png)\n"
        "[About](https://midnightsatin.app/about)\n"
        "[Blog](https://midnightsatin.app/blog/hello)\n"
        "[External](https://example.com/out)\n"
    )
    page = extract_page_signals(
        "https://midnightsatin.app/",
        markdown=markdown,
        metadata={"statusCode": 200},
        title=None,
    )
    assert page.word_count > 0
    assert page.h1_count == 0
    assert page.image_count == 2
    assert page.images_missing_alt == 1
    assert page.internal_links >= 2
    assert page.external_links == 1
    assert "https://midnightsatin.app/about" in page.same_domain_urls

    issues = {item.issue_id: item for item in detect_issues(page)}
    assert "missing-title" in issues
    assert "missing-h1" in issues
    assert "missing-meta-description" in issues
    assert "missing-alt" in issues
    assert "missing-schema" in issues
    assert "how_to_fix" in issues["missing-title"].as_dict()
    score = score_issues(list(issues.values()))
    assert 0 <= score < 100
    one = pick_one_thing(list(issues.values()), owned=True)
    assert "title" in one.lower() or "H1" in one or "heading" in one.lower()


def test_noindex_and_healthy_page() -> None:
    page = extract_page_signals(
        "https://tactic.studio/",
        markdown=(
            "# Industrial AR training\n\n"
            "tactic.studio builds WebAR training modules for plants. " * 20
        ),
        metadata={
            "title": "Industrial AR training | tactic.studio",
            "description": "WebAR and industrial AR training modules for brand and plant teams.",
            "canonical": "https://tactic.studio/",
            "ogTitle": "Industrial AR training | tactic.studio",
            "robots": "index,follow",
            "jsonld": {"@type": "Organization"},
        },
        title="Industrial AR training | tactic.studio",
    )
    issues = detect_issues(page)
    ids = {item.issue_id for item in issues}
    assert "missing-title" not in ids
    assert "missing-h1" not in ids
    assert "noindex" not in ids
    assert page.has_json_ld is True

    blocked = extract_page_signals(
        "https://tactic.studio/secret",
        markdown="# Hidden\n\nNot for search.",
        metadata={"title": "Hidden", "robots": "noindex, nofollow"},
        title="Hidden",
    )
    blocked_ids = {item.issue_id for item in detect_issues(blocked)}
    assert "noindex" in blocked_ids


def test_related_paths_prefer_about() -> None:
    page = extract_page_signals(
        "https://heybuddy.app/",
        markdown=(
            "[Random](https://heybuddy.app/p/123)\n"
            "[About](https://heybuddy.app/about)\n"
            "[Pricing](https://heybuddy.app/pricing)\n"
        ),
        metadata={"title": "HeyBuddy"},
        title="HeyBuddy",
    )
    related = related_paths_to_fetch(page, limit=2)
    assert related[0].endswith("/about")
    assert related[1].endswith("/pricing")
