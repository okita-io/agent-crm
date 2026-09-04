"""Discover vendored skill packs and modules under ``skills/``."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .marketing_skill import skills_root

BRAND_CONTEXT_ID = "brand-context"

# First-run assignments matching the floor mock plus slices runners already load.
DEFAULT_AGENT_SKILLS: dict[str, tuple[str, ...]] = {
    "research": (
        BRAND_CONTEXT_ID,
        "marketing-agi",
        "marketing-agi/positioning",
        "marketing-agi/competitive",
        "marketing-agi/paid-ads",
        "marketing-agi/hooks",
    ),
    "outbound_hunter": (
        BRAND_CONTEXT_ID,
        "marketing-agi",
    ),
    "engagement": (
        BRAND_CONTEXT_ID,
        "social-media",
        "social-media/voice",
        "social-media/post-package",
        "marketing-agi",
        "marketing-agi/slop-patterns",
    ),
    "seo": (
        BRAND_CONTEXT_ID,
        "open-seo",
    ),
    "aeo-geo": (
        BRAND_CONTEXT_ID,
        "aeo-geo",
        "open-seo",
    ),
    "queue-review": ("marketing-agi",),
}


@dataclass(frozen=True)
class SkillRecord:
    id: str
    pack: str
    module: str | None
    label: str
    summary: str
    kind: str
    builtin: bool = True
    virtual: bool = False


def _frontmatter_field(text: str, field: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end < 0:
        return ""
    prefix = f"{field}:"
    for line in text[3:end].splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped[len(prefix) :].strip().strip('"').strip("'")
            return value
    return ""


def _first_prose(text: str, *, max_chars: int = 180) -> str:
    body = text
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end >= 0:
            body = body[end + 4 :]
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|")):
            continue
        if stripped.startswith("**") and stripped.endswith("**"):
            continue
        return stripped[:max_chars]
    return ""


def _summary_for(path: Path) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    description = _frontmatter_field(text, "description")
    if description:
        return description[:240]
    return _first_prose(text)


def _virtual_brand_context() -> SkillRecord:
    return SkillRecord(
        id=BRAND_CONTEXT_ID,
        pack=BRAND_CONTEXT_ID,
        module=None,
        label=BRAND_CONTEXT_ID,
        summary=(
            "Per-brand context files at the repo root. When assigned, prompts "
            "receive a short brand excerpt."
        ),
        kind="pack",
        builtin=True,
        virtual=True,
    )


@lru_cache(maxsize=1)
def list_catalog() -> tuple[SkillRecord, ...]:
    """Packs (``SKILL.md``) and modules (``references/*.md``), plus brand-context."""
    records: list[SkillRecord] = [_virtual_brand_context()]
    root = skills_root()
    if not root.is_dir():
        return tuple(records)
    for pack_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        skill_md = pack_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        pack_id = pack_dir.name
        records.append(
            SkillRecord(
                id=pack_id,
                pack=pack_id,
                module=None,
                label=pack_id,
                summary=_summary_for(skill_md),
                kind="pack",
            )
        )
        refs = pack_dir / "references"
        if not refs.is_dir():
            continue
        for module_path in sorted(refs.glob("*.md")):
            module = module_path.stem
            records.append(
                SkillRecord(
                    id=f"{pack_id}/{module}",
                    pack=pack_id,
                    module=module,
                    label=module,
                    summary=_summary_for(module_path),
                    kind="module",
                )
            )
    return tuple(records)


def catalog_by_id() -> dict[str, SkillRecord]:
    return {record.id: record for record in list_catalog()}


def known_skill_ids() -> frozenset[str]:
    return frozenset(catalog_by_id())


def get_skill(skill_id: str) -> SkillRecord | None:
    return catalog_by_id().get(skill_id.strip())


def skill_label(skill_id: str) -> str:
    record = get_skill(skill_id)
    if record is not None:
        return record.label
    if "/" in skill_id:
        return skill_id.rsplit("/", 1)[-1]
    return skill_id


def sort_skill_ids(skill_ids: list[str] | tuple[str, ...]) -> list[str]:
    """Packs first, then modules, alphabetical within each group."""

    def key(skill_id: str) -> tuple[int, str]:
        return (1 if "/" in skill_id else 0, skill_id)

    return sorted(skill_ids, key=key)
