"""Load vendored marketing-agi skill slices for CRM agent prompts."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .enums import Brand

_SKILLS_SUBDIR = "marketing-agi"
_DEFAULT_MAX_CHARS = 1800


def skills_root() -> Path:
    """Return the skills directory (Docker /app/skills or repo-root skills/)."""
    docker_root = Path("/app/skills")
    if docker_root.is_dir():
        return docker_root
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "skills"


def marketing_skill_root() -> Path:
    return skills_root() / _SKILLS_SUBDIR


def brand_context_path(brand: Brand | None = None) -> Path | None:
    """Resolve brand-context file at repo root."""
    repo_root = Path(__file__).resolve().parents[2]
    if brand is None:
        path = repo_root / "brand-context.md"
        return path if path.is_file() else None

    slug_map = {
        Brand.MIDNIGHTSATIN: "brand-context.midnightsatin.md",
        Brand.CELESTIAL_NEXUS: "brand-context.celestial-nexus.md",
        Brand.HEYBUDDY: "brand-context.heybuddy.md",
        Brand.TACTIC_STUDIO: "brand-context.tactic-studio.md",
    }
    filename = slug_map.get(brand)
    if not filename:
        return None
    path = repo_root / filename
    return path if path.is_file() else None


@lru_cache(maxsize=32)
def _read_bounded(path: Path, *, max_chars: int) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rsplit("\n", 1)[0].strip()
    return truncated + "\n\n[…truncated for prompt budget]"


def _extract_section(
    path: Path,
    *,
    start_heading: str,
    end_heading: str | None = None,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    start = text.find(start_heading)
    if start < 0:
        return _read_bounded(path, max_chars=max_chars)
    end = len(text)
    if end_heading:
        end_idx = text.find(end_heading, start + len(start_heading))
        if end_idx > start:
            end = end_idx
    section = text[start:end].strip()
    if len(section) > max_chars:
        section = section[:max_chars].rsplit("\n", 1)[0].strip()
        section += "\n\n[…truncated for prompt budget]"
    return section


def load_reference_slice(
    relative_path: str,
    *,
    max_chars: int = _DEFAULT_MAX_CHARS,
    start_heading: str | None = None,
    end_heading: str | None = None,
) -> str:
    """Load a bounded excerpt from skills/marketing-agi/references/."""
    path = marketing_skill_root() / relative_path
    if start_heading:
        return _extract_section(
            path,
            start_heading=start_heading,
            end_heading=end_heading,
            max_chars=max_chars,
        )
    return _read_bounded(path, max_chars=max_chars)


def competitor_summarizer_guidance(
    *,
    include_competitive: bool = True,
    include_positioning: bool = True,
) -> str:
    """Bounded competitive + positioning guidance for Research competitor kind."""
    competitive = (
        load_reference_slice(
            "references/competitive.md",
            start_heading="## The teardown protocol",
            end_heading="## Cross-competitor synthesis",
            max_chars=1600,
        )
        if include_competitive
        else ""
    )
    positioning = (
        load_reference_slice(
            "references/positioning.md",
            start_heading="## Positioning",
            end_heading="## Offer design",
            max_chars=700,
        )
        if include_positioning
        else ""
    )
    parts = [
        "Follow the marketing-agi competitive teardown protocol (public sources only).",
        "Label inference; never invent stats, testimonials, or spend data.",
        "State gaps explicitly with [NEED: x] when proof is missing.",
    ]
    if competitive:
        parts.append(f"--- competitive.md (excerpt) ---\n{competitive}")
    if positioning:
        parts.append(f"--- positioning.md (excerpt) ---\n{positioning}")
    return "\n\n".join(parts)


def ad_placement_summarizer_guidance(
    *,
    include_paid_ads: bool = True,
    include_hooks: bool = True,
) -> str:
    """Bounded paid-ads + hooks guidance for ad-placement discovery briefs."""
    paid_ads = (
        load_reference_slice(
            "references/paid-ads.md",
            start_heading="## Before you start",
            end_heading="## Step 1 — Classify by concept",
            max_chars=700,
        )
        if include_paid_ads
        else ""
    )
    paid_brief = (
        load_reference_slice(
            "references/paid-ads.md",
            start_heading="## Step 4 — Ranked production brief",
            end_heading="## Production brief",
            max_chars=500,
        )
        if include_paid_ads
        else ""
    )
    hooks = (
        load_reference_slice(
            "references/hooks.md",
            start_heading="## The three components",
            end_heading="## Grounding — before generating anything",
            max_chars=600,
        )
        if include_hooks
        else ""
    )
    parts = [
        "Discovery only — never buy ads, log into ad accounts, or invent pricing/contacts.",
        "Assess brand fit and brand safety honestly. Write brief-ready notes when evidence supports it.",
        "Flag high-traffic forums and communities as engagement surfaces for later comment drafts.",
        "Never invent proof; use [NEED: x] for missing data.",
    ]
    if paid_ads:
        parts.append(f"--- paid-ads.md (excerpt) ---\n{paid_ads}")
    if paid_brief:
        parts.append(f"--- paid-ads brief craft (excerpt) ---\n{paid_brief}")
    if hooks:
        parts.append(f"--- hooks.md (excerpt) ---\n{hooks}")
    return "\n\n".join(parts)


def brand_context_snippet(brand: Brand, *, max_chars: int = 600) -> str:
    """Short brand-context excerpt for summarizer prompts."""
    path = brand_context_path(brand)
    if path is None:
        return ""
    return _read_bounded(path, max_chars=max_chars)


def skill_file_exists(relative_path: str) -> bool:
    return (marketing_skill_root() / relative_path).is_file()
