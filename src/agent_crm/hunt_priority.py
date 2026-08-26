"""Hunt query dequeue priority (higher integer = runs first)."""

from __future__ import annotations

from .enums import Brand, ContactAudience

# Higher number dequeues before lower. Unlisted brand/audience pairs use DEFAULT_PRIORITY.
DEFAULT_HUNT_QUERY_PRIORITY = 30

_PRIORITY_TABLE: dict[tuple[Brand, ContactAudience | None], int] = {
    (Brand.TACTIC_STUDIO, ContactAudience.MARKETING): 100,
    (Brand.TACTIC_STUDIO, ContactAudience.INFLUENCER): 90,
    (Brand.TACTIC_STUDIO, ContactAudience.USER): 80,
    (Brand.MIDNIGHTSATIN, ContactAudience.INFLUENCER): 70,
    (Brand.MIDNIGHTSATIN, ContactAudience.USER): 65,
    (Brand.CELESTIAL_NEXUS, ContactAudience.INFLUENCER): 60,
    (Brand.CELESTIAL_NEXUS, ContactAudience.USER): 55,
    (Brand.HEYBUDDY, ContactAudience.INFLUENCER): 50,
    (Brand.HEYBUDDY, ContactAudience.USER): 45,
}


def hunt_query_priority(
    brand: Brand,
    audience: ContactAudience | None,
) -> int:
    """Return dequeue priority for a hunt query (higher runs first)."""
    return _PRIORITY_TABLE.get((brand, audience), DEFAULT_HUNT_QUERY_PRIORITY)
