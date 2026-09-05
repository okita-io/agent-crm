"""Tests for vendored social-media skill loading."""

from __future__ import annotations

from agent_crm.social_skill import (
    content_matrix_guidance,
    engagement_draft_guidance,
    niche_pulse_guidance,
    post_package_guidance,
    skill_file_exists,
    social_skill_root,
)


def test_social_skill_files_exist() -> None:
    root = social_skill_root()
    assert root.is_dir()
    assert (root / "SKILL.md").is_file()
    assert (root / "LICENSE").is_file()
    assert (root / "SOURCE").is_file()
    for name in (
        "references/voice.md",
        "references/content-matrix.md",
        "references/niche-pulse.md",
        "references/post-package.md",
        "references/newsletter.md",
        "references/visual-briefs.md",
        "references/profile.md",
    ):
        assert skill_file_exists(name), name
    reference_files = list((root / "references").glob("*.md"))
    assert len(reference_files) == 7


def test_engagement_draft_guidance_is_helpful_first_not_persona() -> None:
    guidance = engagement_draft_guidance()
    lowered = guidance.lower()
    assert "helpful first" in lowered
    assert "never claim the comment was posted" in lowered
    assert "publisher" in lowered or "human schedule" in lowered
    assert "charlie hills" in lowered
    assert "pinned-comment meme" in lowered
    assert "slop-patterns" in lowered
    assert "not x, but y" in lowered or "not just x" in lowered


def test_post_package_guidance_keeps_links_in_first_comment() -> None:
    guidance = post_package_guidance()
    lowered = guidance.lower()
    assert "first comment" in lowered
    assert "do not post" in lowered
    assert "never invent proof" in lowered
    assert "publisher" in lowered or "human schedule" in lowered


def test_content_matrix_and_niche_pulse_guidance_load() -> None:
    matrix = content_matrix_guidance()
    assert "pillars" in matrix.lower()
    assert "[need:" in matrix.lower() or "need:" in matrix.lower()
    pulse = niche_pulse_guidance()
    assert "7-day" in pulse.lower() or "last 7 days" in pulse.lower()
    assert "searxng" in pulse.lower()
