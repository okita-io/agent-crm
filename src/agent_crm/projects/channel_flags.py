"""Channel arming helpers for standing loops."""

from __future__ import annotations

import logging
from pathlib import Path

from agent_crm.enums import Brand

from .schema import CHANNEL_NAMES, ProjectChannelName, ProjectDocument
from .store import brand_for_slug, cache_generation, list_projects, projects_dir

logger = logging.getLogger(__name__)

PROJECT_CHANNELS: tuple[ProjectChannelName, ...] = CHANNEL_NAMES

_FALLBACK_BRANDS: tuple[Brand, ...] = (
    Brand.CELESTIAL_NEXUS,
    Brand.MIDNIGHTSATIN,
    Brand.HEYBUDDY,
    Brand.TACTIC_STUDIO,
)

_cache_key: tuple[int, float] | None = None
_cached_docs: list[ProjectDocument] | None = None


def _dir_mtime(root: Path) -> float:
    if not root.is_dir():
        return -1.0
    newest = 0.0
    try:
        for path in root.glob("*.yaml"):
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        return -1.0
    return newest


def _cached_projects() -> list[ProjectDocument] | None:
    """Return project docs, or None when the catalog is missing (fail-open)."""
    global _cache_key, _cached_docs
    root = projects_dir()
    if not root.is_dir():
        return None
    key = (cache_generation(), _dir_mtime(root))
    if _cached_docs is not None and _cache_key == key:
        return _cached_docs
    docs = list_projects()
    _cached_docs = docs
    _cache_key = key
    return docs


def active_brands_for(channel: ProjectChannelName) -> tuple[Brand, ...]:
    """Brands whose project YAML arms ``channel`` (and master enabled).

    Fail-open: if the projects directory is missing, return the historical
    four-brand tuple so existing tests keep working without YAML fixtures.
    """
    if channel not in CHANNEL_NAMES:
        raise ValueError(f"unknown channel: {channel}")
    docs = _cached_projects()
    if docs is None:
        return _FALLBACK_BRANDS
    brands: list[Brand] = []
    seen: set[Brand] = set()
    for doc in docs:
        if not doc.enabled:
            continue
        ch = doc.channels.get(channel)
        if ch is None or not ch.armed:
            continue
        brand = brand_for_slug(doc.slug)
        if brand is None or brand in seen:
            continue
        seen.add(brand)
        brands.append(brand)
    return tuple(brands)


def project_prompt_for(
    brand: Brand,
    channel: ProjectChannelName | None = None,
    *,
    max_chars: int = 1200,
) -> str:
    """Origin + optional task prompt + bounded brand-context excerpt."""
    from agent_crm.marketing_skill import brand_context_snippet

    docs = _cached_projects()
    doc: ProjectDocument | None = None
    if docs is not None:
        for item in docs:
            if item.slug == brand.value:
                doc = item
                break

    parts: list[str] = []
    if doc is not None:
        origin = (doc.origin_prompt or "").strip()
        if origin:
            parts.append(origin)
        if channel is not None:
            ch = doc.channels.get(channel)
            if ch is not None:
                task = (ch.prompt or "").strip()
                if task:
                    parts.append(task)

    file_excerpt = brand_context_snippet(brand, max_chars=min(600, max_chars))
    if file_excerpt:
        # Avoid duplicating when origin was reloaded from the same file.
        if not parts or file_excerpt[:80] not in parts[0]:
            parts.append(file_excerpt)

    text = "\n\n".join(parts).strip()
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rsplit("\n", 1)[0].strip()
    return truncated + "\n\n[…truncated for prompt budget]"


def clear_channel_cache() -> None:
    global _cache_key, _cached_docs
    _cache_key = None
    _cached_docs = None
