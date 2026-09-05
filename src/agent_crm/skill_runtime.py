"""Honor assigned skills when building agent prompts."""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError

from .enums import Brand
from .errors import NotFoundError
from .marketing_skill import (
    ad_placement_summarizer_guidance,
    competitor_summarizer_guidance,
)
from .skill_catalog import BRAND_CONTEXT_ID
from .skill_store import list_agent_skills

logger = logging.getLogger(__name__)


def uses_skill(assigned: set[str] | frozenset[str], skill_id: str) -> bool:
    """True when ``skill_id`` is assigned, or its pack is assigned with no module whitelist."""
    if skill_id in assigned:
        return True
    if "/" not in skill_id:
        return False
    pack = skill_id.split("/", 1)[0]
    if pack not in assigned:
        return False
    return not any(item.startswith(f"{pack}/") for item in assigned)


def assigned_set(agent_name: str) -> frozenset[str] | None:
    """Assigned ids, or ``None`` when the store is unavailable (fail open)."""
    try:
        return frozenset(list_agent_skills(agent_name))
    except NotFoundError:
        return frozenset()
    except (SQLAlchemyError, OSError):
        logger.debug("skill assignments unavailable for %s", agent_name, exc_info=True)
        return None


def has_skill(agent_name: str, skill_id: str) -> bool:
    """Whether ``agent_name`` may use ``skill_id``. Fail-open when the DB is missing."""
    assigned = assigned_set(agent_name)
    if assigned is None:
        return True
    return uses_skill(assigned, skill_id)


def brand_context_for(
    agent_name: str,
    brand: Brand,
    *,
    max_chars: int = 600,
    channel: str | None = None,
) -> str:
    """Brand excerpt when the agent still has the virtual ``brand-context`` skill.

    Prefers the project YAML origin + task prompt when present.
    """
    if not has_skill(agent_name, BRAND_CONTEXT_ID):
        return ""
    from agent_crm.projects.channel_flags import project_prompt_for

    return project_prompt_for(brand, channel, max_chars=max_chars)  # type: ignore[arg-type]


def research_competitor_guidance() -> str:
    pack = has_skill("research", "marketing-agi")
    competitive = has_skill("research", "marketing-agi/competitive")
    positioning = has_skill("research", "marketing-agi/positioning")
    if not (pack or competitive or positioning):
        return ""
    return competitor_summarizer_guidance(
        include_competitive=competitive,
        include_positioning=positioning,
    )


def research_ad_placement_guidance() -> str:
    pack = has_skill("research", "marketing-agi")
    paid_ads = has_skill("research", "marketing-agi/paid-ads")
    hooks = has_skill("research", "marketing-agi/hooks")
    if not (pack or paid_ads or hooks):
        return ""
    return ad_placement_summarizer_guidance(
        include_paid_ads=paid_ads,
        include_hooks=hooks,
    )
