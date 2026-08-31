"""Load vendored AEO/GEO skill slices for The Agency document prompts."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_SKILLS_SUBDIR = "aeo-geo"
_DEFAULT_MAX_CHARS = 1800


def skills_root() -> Path:
    docker_root = Path("/app/skills")
    if docker_root.is_dir():
        return docker_root
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "skills"


def aeo_geo_skill_root() -> Path:
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
    return _read_bounded(aeo_geo_skill_root() / relative_path, max_chars=max_chars)


def review_writer_guidance() -> str:
    audit = load_reference_slice("references/aeo-geo-review.md", max_chars=1600)
    parts = [
        "Write an AEO/GEO review document. Do not implement changes on any website.",
        (
            "AEO = extractable answers (snippets, some AI Overviews). "
            "GEO = being cited or mentioned inside generated chat answers."
        ),
        (
            "Ground claims in supplied page signals. Never invent citation counts, "
            "mention share, or Search Console generative-AI numbers. "
            "Use [NEED: x] for missing data."
        ),
        "The report exists to support ONE action the owner can take this week.",
        "Mentions are not citations. Schema is table stakes, not a cheat code.",
        "tactic.studio outreach: research/docs only — no outreach steps.",
    ]
    if audit:
        parts.append(f"--- aeo-geo-review.md (excerpt) ---\n{audit}")
    return "\n\n".join(parts)


def plan_writer_guidance() -> str:
    plan = load_reference_slice("references/aeo-geo-plan.md", max_chars=1600)
    parts = [
        "Write an AEO/GEO implementation plan for a human to apply on the target site.",
        (
            "Never claim the work was deployed. Each task must include the page URL, "
            "exact copy or markup to add, effort S/M/L, and how to verify."
        ),
        (
            "Follow operating order: access → entity kit → quotable pages → "
            "fan-out → optional llms.txt → measurement panel."
        ),
        "Do not include login, DNS, or hosting credentials. Do not invent proof.",
    ]
    if plan:
        parts.append(f"--- aeo-geo-plan.md (excerpt) ---\n{plan}")
    return "\n\n".join(parts)


def skill_file_exists(relative_path: str) -> bool:
    return (aeo_geo_skill_root() / relative_path).is_file()
