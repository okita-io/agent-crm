"""Load vendored OpenSEO skill slices for CRM SEO document prompts."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_SKILLS_SUBDIR = "open-seo"
_DEFAULT_MAX_CHARS = 1800


def skills_root() -> Path:
    docker_root = Path("/app/skills")
    if docker_root.is_dir():
        return docker_root
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "skills"


def open_seo_skill_root() -> Path:
    return skills_root() / _SKILLS_SUBDIR


@lru_cache(maxsize=16)
def _read_bounded(path: Path, *, max_chars: int) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rsplit("\n", 1)[0].strip()
    return truncated + "\n\n[…truncated for prompt budget]"


def load_reference_slice(relative_path: str, *, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    return _read_bounded(open_seo_skill_root() / relative_path, max_chars=max_chars)


def review_writer_guidance() -> str:
    audit = load_reference_slice("references/site-audit.md", max_chars=1600)
    parts = [
        "Write an SEO review document. Do not implement changes on any website.",
        (
            "Ground claims in the supplied page signals and issues. Never invent backlinks, "
            "rankings, traffic, or Search Console numbers. Use [NEED: x] for missing data."
        ),
        "The report exists to support ONE action the owner can take this week.",
        "Gloss jargon on first use. Scores are heuristics, not vendor rankings.",
    ]
    if audit:
        parts.append(f"--- site-audit.md (excerpt) ---\n{audit}")
    return "\n\n".join(parts)


def plan_writer_guidance() -> str:
    plan = load_reference_slice("references/seo-plan.md", max_chars=1600)
    parts = [
        "Write an SEO implementation plan for a human to apply on the target site.",
        (
            "Never claim the work was deployed. Each task must include the page URL, the "
            "exact copy or markup to add, effort S/M/L, and how to verify."
        ),
        "Do not include login, DNS, or hosting credentials. Do not invent proof.",
    ]
    if plan:
        parts.append(f"--- seo-plan.md (excerpt) ---\n{plan}")
    return "\n\n".join(parts)


def skill_file_exists(relative_path: str) -> bool:
    return (open_seo_skill_root() / relative_path).is_file()
