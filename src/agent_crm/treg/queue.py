"""Enqueue allowed treg tools as hunter or research follow-up work."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select

from agent_crm.enums import Brand, ResearchFindingKind
from agent_crm.hunt.seeds import seeds_for_brand
from agent_crm.hunt.store import HuntStore
from agent_crm.models import CommentPerson, ContactProfile, TregTool
from agent_crm.research.query_store import ResearchQueryStore
from agent_crm.research.seeds import default_kind_for_brand, seed_queries
from .search import treg_origin
from .store import get_treg_tool, set_treg_tools_allowed

_WORK_BRANDS: tuple[Brand, ...] = (
    Brand.TACTIC_STUDIO,
    Brand.MIDNIGHTSATIN,
    Brand.CELESTIAL_NEXUS,
    Brand.HEYBUDDY,
)


def _work_brands() -> tuple[Brand, ...]:
    from agent_crm.projects.channel_flags import active_brands_for

    return active_brands_for("hunter") or _WORK_BRANDS

_SEARCH_PARAM_NAMES = {
    "q",
    "query",
    "keyword",
    "keywords",
    "search",
    "term",
    "text",
}

_PERSON_PARAM_NAMES = {
    "full_name",
    "first_name",
    "last_name",
    "email",
    "linkedin_url",
    "linkedin_handle",
    "domain",
    "name",
}


@dataclass
class TregAllowResult:
    allowed: list[str] = field(default_factory=list)
    hunt_enqueued: int = 0
    research_enqueued: int = 0
    skipped: list[str] = field(default_factory=list)


def schema_param_names(input_schema: dict | None) -> set[str]:
    if not isinstance(input_schema, dict):
        return set()
    names: set[str] = set()
    for bucket in ("queryParams", "body"):
        spec = input_schema.get(bucket)
        if isinstance(spec, dict):
            names.update(str(key) for key in spec)
    return names


def endpoint_accepts_search_query(input_schema: dict | None) -> bool:
    return bool(schema_param_names(input_schema) & _SEARCH_PARAM_NAMES)


def endpoint_accepts_person(input_schema: dict | None) -> bool:
    return bool(schema_param_names(input_schema) & _PERSON_PARAM_NAMES)


def allow_treg_tools(endpoint_ids: list[str]) -> TregAllowResult:
    """Allow paid endpoints and queue hunter/research follow-ups."""
    result = TregAllowResult()
    updated = set_treg_tools_allowed(endpoint_ids, allowed=True)
    result.allowed = updated
    hunt = HuntStore()
    research = ResearchQueryStore()
    now = datetime.now(UTC)
    for endpoint_id in updated:
        tool = get_treg_tool(endpoint_id)
        if tool is None:
            result.skipped.append(endpoint_id)
            continue
        if tool.queue_as == "skip":
            result.skipped.append(endpoint_id)
            continue
        hunt_added, research_added = _enqueue_tool(tool, hunt=hunt, research=research)
        result.hunt_enqueued += hunt_added
        result.research_enqueued += research_added
        if hunt_added or research_added:
            _touch_queued_at(endpoint_id, now)
    return result


def enqueue_free_treg_tools(*, limit: int = 40) -> TregAllowResult:
    """Queue auto-allowed free discovery tools (not utilities)."""
    from .store import list_treg_tools

    result = TregAllowResult()
    hunt = HuntStore()
    research = ResearchQueryStore()
    queued = 0
    for tool in list_treg_tools(paid=False):
        if queued >= limit:
            break
        if tool.queue_as == "skip":
            continue
        hunt_added, research_added = _enqueue_tool(tool, hunt=hunt, research=research)
        result.hunt_enqueued += hunt_added
        result.research_enqueued += research_added
        if hunt_added or research_added:
            result.allowed.append(tool.endpoint_id)
            queued += 1
    return result


def _enqueue_tool(
    tool: TregTool,
    *,
    hunt: HuntStore,
    research: ResearchQueryStore,
) -> tuple[int, int]:
    origin = treg_origin(
        tool.endpoint_id,
        paid=not tool.is_free,
        queue_as=tool.queue_as,
    )
    params = {
        "treg_endpoint_id": tool.endpoint_id,
        "treg_paid": not tool.is_free,
        "treg_method": tool.method,
    }
    hunt_added = 0
    research_added = 0
    accepts_search = endpoint_accepts_search_query(tool.input_schema)
    accepts_person = endpoint_accepts_person(tool.input_schema)

    if tool.queue_as == "research" or (tool.queue_as == "hunter" and accepts_search):
        for brand in _work_brands():
            query = _seed_query_for(brand, tool.queue_as)
            if not query:
                continue
            if tool.queue_as == "research":
                kind = default_kind_for_brand(brand)
                if research.enqueue_query(
                    query=query,
                    brand=brand,
                    kind=kind,
                    origin=origin,
                ):
                    research_added += 1
            else:
                if hunt.enqueue_query(
                    query=query,
                    brand=brand,
                    origin=origin,
                    params=params,
                ):
                    hunt_added += 1

    if tool.queue_as == "hunter" and accepts_person:
        for brand, query, extra in _person_followups(brand_limit=2):
            merged = dict(params)
            merged.update(extra)
            if hunt.enqueue_query(
                query=query,
                brand=brand,
                origin=origin,
                params=merged,
            ):
                hunt_added += 1

    if hunt_added == 0 and research_added == 0 and accepts_search:
        # One unassigned seed so the tool still has work even without brand packs.
        query = "people to follow up"
        if tool.queue_as == "research":
            if research.enqueue_query(
                query=query,
                brand=Brand.UNASSIGNED,
                kind=ResearchFindingKind.OTHER,
                origin=origin,
            ):
                research_added += 1
        elif hunt.enqueue_query(
            query=query,
            brand=Brand.UNASSIGNED,
            origin=origin,
            params=params,
        ):
            hunt_added += 1
    return hunt_added, research_added


def _seed_query_for(brand: Brand, queue_as: str) -> str | None:
    if queue_as == "research":
        queries = seed_queries(brand, default_kind_for_brand(brand))
        return queries[0] if queries else None
    seeds = seeds_for_brand(brand)
    return seeds[0] if seeds else None


def _person_followups(*, brand_limit: int) -> list[tuple[Brand, str, dict]]:
    """Use existing CRM people as paid-tool follow-ups (name + domain/email)."""
    from agent_crm.db import session_scope
    from agent_crm.hunt.utils import registrable_domain

    rows: list[tuple[Brand, str, dict]] = []
    with session_scope() as session:
        profiles = list(
            session.scalars(
                select(ContactProfile)
                .where(ContactProfile.name.is_not(None))
                .order_by(ContactProfile.updated_at.desc())
                .limit(brand_limit * len(_work_brands()))
            )
        )
        for profile in profiles:
            name = (profile.name or "").strip()
            if not name:
                continue
            extra: dict[str, str] = {"full_name": name}
            if profile.email:
                extra["email"] = profile.email
                if "@" in profile.email:
                    extra["domain"] = profile.email.rsplit("@", 1)[-1]
            socials = profile.socials if isinstance(profile.socials, dict) else {}
            linkedin = socials.get("linkedin") if isinstance(socials, dict) else None
            if isinstance(linkedin, str) and linkedin:
                extra["linkedin_url"] = linkedin
            rows.append((profile.brand, name, extra))

        if len(rows) < 4:
            people = list(
                session.scalars(
                    select(CommentPerson)
                    .where(CommentPerson.display_name.is_not(None))
                    .order_by(CommentPerson.updated_at.desc())
                    .limit(8)
                )
            )
            for person in people:
                name = (person.display_name or person.handle or "").strip()
                if not name:
                    continue
                extra = {"full_name": name}
                if person.profile_url:
                    extra["linkedin_url"] = person.profile_url
                    domain = registrable_domain(person.profile_url)
                    if domain:
                        extra["domain"] = domain
                rows.append((person.brand, name, extra))
    return rows[: brand_limit * 2]


def _touch_queued_at(endpoint_id: str, when: datetime) -> None:
    from agent_crm.db import session_scope

    with session_scope() as session:
        row = session.get(TregTool, endpoint_id)
        if row is not None:
            row.queued_at = when
