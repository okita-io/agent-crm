"""Load vendored social-media skill slices for CRM agent prompts."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .marketing_skill import load_reference_slice as load_marketing_slice
from .marketing_skill import skills_root

_SKILLS_SUBDIR = "social-media"
_DEFAULT_MAX_CHARS = 1800


def social_skill_root() -> Path:
    return skills_root() / _SKILLS_SUBDIR


@lru_cache(maxsize=32)
def _read_bounded(path: Path, *, max_chars: int) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rsplit("\n", 1)[0].strip()
    return truncated + "\n\n[…truncated for prompt budget]"


def load_reference_slice(
    relative_path: str,
    *,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """Load a bounded excerpt from skills/social-media/."""
    return _read_bounded(social_skill_root() / relative_path, max_chars=max_chars)


def skill_file_exists(relative_path: str) -> bool:
    return (social_skill_root() / relative_path).is_file()


def engagement_draft_guidance() -> str:
    """Bounded first-comment / helpful-first rules for forum reply drafts."""
    post_pkg = load_reference_slice(
        "references/post-package.md",
        max_chars=900,
    )
    slop = load_marketing_slice(
        "references/slop-patterns.md",
        start_heading="## Structural tells",
        end_heading="## Lexical tells",
        max_chars=500,
    )
    parts = [
        "Forum replies are not owned-social posts. Helpful first; mention the product "
        "only when it naturally answers the thread.",
        "Never claim the comment was posted from this draft agent. Publish only "
        "via the publisher after human schedule. No emails, no login-wall URLs, "
        "no immediate buy CTAs, no Charlie Hills pinned-comment meme format.",
        "Do not paste a first-comment resource CTA onto someone else's thread.",
        "Never invent proof; use [NEED: x] only in internal notes, not in the public draft.",
    ]
    if post_pkg:
        parts.append(f"--- post-package.md (excerpt) ---\n{post_pkg}")
    if slop:
        parts.append(f"--- slop-patterns.md (excerpt) ---\n{slop}")
    return "\n\n".join(parts)


def post_package_guidance() -> str:
    """Owned-social post document writer."""
    post_pkg = load_reference_slice("references/post-package.md", max_chars=1400)
    social = load_marketing_slice(
        "references/social.md",
        start_heading="## The hook",
        end_heading="## LinkedIn mechanics",
        max_chars=700,
    )
    parts = [
        "Write a content-package document. Do not post from this skill; "
        "publisher sends only after human schedule.",
        "Links belong in the first comment, not the post body.",
        "Never invent proof. Flag unverified claims.",
    ]
    if post_pkg:
        parts.append(f"--- post-package.md (excerpt) ---\n{post_pkg}")
    if social:
        parts.append(f"--- social.md hook (excerpt) ---\n{social}")
    return "\n\n".join(parts)


def content_matrix_guidance() -> str:
    matrix = load_reference_slice("references/content-matrix.md", max_chars=1400)
    parts = [
        "Write a pillars × formats idea table. Specific headlines, not themes.",
        "Do not invent proof cells; use [NEED: x]. Do not write 32 full posts.",
    ]
    if matrix:
        parts.append(f"--- content-matrix.md (excerpt) ---\n{matrix}")
    return "\n\n".join(parts)


def niche_pulse_guidance() -> str:
    pulse = load_reference_slice("references/niche-pulse.md", max_chars=1400)
    parts = [
        "7-day niche pulse from SearXNG + Firecrawl only. Never invent dates or links.",
        "Exclude items without a verified publish date inside the window.",
    ]
    if pulse:
        parts.append(f"--- niche-pulse.md (excerpt) ---\n{pulse}")
    return "\n\n".join(parts)
