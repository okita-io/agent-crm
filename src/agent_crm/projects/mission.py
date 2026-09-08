"""Mission / focus prompt helpers for standing agent loops."""

from __future__ import annotations

import logging
import re

from agent_crm.enums import Brand

from .channel_flags import project_prompt_for
from .schema import ProjectChannelName

logger = logging.getLogger(__name__)

_QUERY_LINE_RE = re.compile(r"^\s*(?:query|seed)\s*:\s*(.+)$", re.IGNORECASE)


def mission_focus_for(
    brand: Brand,
    channel: ProjectChannelName,
    *,
    max_chars: int = 1200,
) -> str:
    """Origin + channel task prompt + brand-context excerpt for one loop."""
    return project_prompt_for(brand, channel, max_chars=max_chars)


def log_mission_focus(actor: str, brand: Brand, channel: ProjectChannelName) -> str:
    """Log and return the mission focus text an agent cycle will use."""
    focus = mission_focus_for(brand, channel, max_chars=600)
    if focus:
        preview = focus.replace("\n", " ")[:160]
        logger.info("%s mission focus for %s: %s", actor, brand.value, preview)
    else:
        logger.info("%s mission focus for %s: (none configured)", actor, brand.value)
    return focus


def supplemental_seed_queries(
    brand: Brand,
    channel: ProjectChannelName,
    *,
    max_queries: int = 12,
) -> list[str]:
    """Parse ``query:`` / ``seed:`` lines from a channel prompt as hunt/research seeds.

    Operators can add runnable search strings directly in the Projects prompt editor;
    standing loops merge these with code seed packs each cycle.
    """
    from .channel_flags import _cached_projects

    docs = _cached_projects()
    if docs is None:
        return []
    doc = next((item for item in docs if item.slug == brand.value), None)
    if doc is None:
        return []
    ch = doc.channels.get(channel)
    if ch is None:
        return []
    prompt = (ch.prompt or "").strip()
    if not prompt:
        return []

    queries: list[str] = []
    for line in prompt.splitlines():
        match = _QUERY_LINE_RE.match(line)
        if not match:
            continue
        query = match.group(1).strip()
        if len(query) >= 8:
            queries.append(query[:200])
        if len(queries) >= max_queries:
            break
    return queries
