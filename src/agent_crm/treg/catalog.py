"""Classify treg catalog rows as free vs paid hunter/research tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CATALOG_SEARCHES: tuple[tuple[str, str], ...] = (
    ("hunter", "find work email"),
    ("hunter", "enrich a person linkedin"),
    ("hunter", "linkedin profile"),
    ("hunter", "company employees"),
    ("hunter", "tiktok user profile"),
    ("hunter", "instagram user profile"),
    ("hunter", "twitter user profile"),
    ("research", "google organic search results"),
    ("research", "web search results serp"),
    ("research", "reddit posts"),
    ("research", "tiktok search videos"),
    ("research", "instagram hashtag posts"),
    ("research", "company news"),
    ("free", "free"),
    ("free", "disposable email domain"),
)

_SKIP_CAPABILITY_FRAGMENTS: tuple[str, ...] = (
    "job.list",
    "job.results",
    "locations",
    "locations-country",
    "account",
    "billing",
    "oauth",
    "webhook",
)

_SKIP_KINDS: frozenset[str] = frozenset({"utility", "management"})

_HUNTER_CAPABILITY_FRAGMENTS: tuple[str, ...] = (
    "people.",
    "contacts.",
    "employees",
    "email.find",
    "email.reveal",
    "identity.resolve",
    "phone.find",
    "profile",
)

_RESEARCH_CAPABILITY_FRAGMENTS: tuple[str, ...] = (
    "serp",
    "search",
    "posts",
    "videos",
    "tweets",
    "news",
    "backlinks",
    "keyword",
)

_HUNTER_PLATFORMS: frozenset[str] = frozenset({"people", "linkedin"})
_RESEARCH_PLATFORMS: frozenset[str] = frozenset(
    {
        "google",
        "bing",
        "yahoo",
        "web",
        "reddit",
        "tiktok",
        "instagram",
        "twitter",
        "x",
        "youtube",
        "companies",
    }
)


@dataclass(frozen=True)
class CatalogTool:
    endpoint_id: str
    title: str
    summary: str
    provider: str
    capability: str
    platform: str
    method: str
    path: str
    kind: str
    queue_as: str
    estimated_cost_usd: float | None
    cost_type: str
    cost_note: str | None
    is_free: bool
    is_routed: bool
    selectable: bool
    input_schema: dict[str, Any] | None


def cost_usd(cost: Any) -> float | None:
    if not isinstance(cost, dict):
        return None
    raw = cost.get("usd")
    if raw is None:
        raw = cost.get("value")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def is_free_cost(cost: Any) -> bool:
    if not isinstance(cost, dict):
        return False
    if str(cost.get("type") or "").lower() == "free":
        return True
    usd = cost_usd(cost)
    return usd == 0.0


def classify_queue_as(row: dict[str, Any], *, hint: str | None = None) -> str:
    """Return hunter, research, or skip for a catalog search hit."""
    kind = str(row.get("kind") or "").lower()
    capability = str(row.get("capability") or "").lower()
    platform = str(row.get("platform") or "").lower()
    endpoint_id = str(row.get("id") or "").lower()
    if kind in _SKIP_KINDS:
        return "skip"
    if any(fragment in capability for fragment in _SKIP_CAPABILITY_FRAGMENTS):
        return "skip"
    if any(fragment in endpoint_id for fragment in _SKIP_CAPABILITY_FRAGMENTS):
        return "skip"
    if platform in _HUNTER_PLATFORMS or any(
        fragment in capability for fragment in _HUNTER_CAPABILITY_FRAGMENTS
    ):
        return "hunter"
    if platform in _RESEARCH_PLATFORMS or any(
        fragment in capability for fragment in _RESEARCH_CAPABILITY_FRAGMENTS
    ):
        return "research"
    if hint in {"hunter", "research"}:
        return hint
    return "skip"


def catalog_tool_from_row(row: dict[str, Any], *, hint: str | None = None) -> CatalogTool | None:
    endpoint_id = str(row.get("id") or "").strip()
    if not endpoint_id:
        return None
    cost = row.get("cost") if isinstance(row.get("cost"), dict) else {}
    free = is_free_cost(cost)
    queue_as = classify_queue_as(row, hint=hint)
    if queue_as == "skip" and not free:
        return None
    if queue_as == "skip" and free:
        # Keep free utilities that hunter/research might still call later.
        queue_as = "skip"
    input_schema = row.get("input") if isinstance(row.get("input"), dict) else None
    return CatalogTool(
        endpoint_id=endpoint_id,
        title=str(row.get("name") or endpoint_id)[:512],
        summary=str(row.get("summary") or "")[:4000],
        provider=str(row.get("provider") or "")[:128],
        capability=str(row.get("capability") or "")[:255],
        platform=str(row.get("platform") or "")[:64],
        method=str(row.get("method") or "GET").upper()[:16],
        path=str(row.get("path") or "")[:512],
        kind=str(row.get("kind") or "data")[:32],
        queue_as=queue_as,
        estimated_cost_usd=cost_usd(cost),
        cost_type=str(cost.get("type") or "unknown")[:32],
        cost_note=(str(cost["note"])[:2000] if cost.get("note") else None),
        is_free=free,
        is_routed=str(row.get("kind") or "").lower() == "routed"
        or endpoint_id.startswith("treg."),
        selectable=True,
        input_schema=input_schema,
    )


def mark_selectable(tools: list[CatalogTool]) -> list[CatalogTool]:
    """Prefer routed jobs on the picker; hide child providers of the same capability."""
    routed_capabilities = {
        tool.capability for tool in tools if tool.is_routed and tool.capability
    }
    updated: list[CatalogTool] = []
    for tool in tools:
        hide_child = (
            bool(tool.capability)
            and tool.capability in routed_capabilities
            and not tool.is_routed
            and not tool.is_free
        )
        if not hide_child:
            updated.append(tool)
            continue
        updated.append(
            CatalogTool(
                endpoint_id=tool.endpoint_id,
                title=tool.title,
                summary=tool.summary,
                provider=tool.provider,
                capability=tool.capability,
                platform=tool.platform,
                method=tool.method,
                path=tool.path,
                kind=tool.kind,
                queue_as=tool.queue_as,
                estimated_cost_usd=tool.estimated_cost_usd,
                cost_type=tool.cost_type,
                cost_note=tool.cost_note,
                is_free=tool.is_free,
                is_routed=tool.is_routed,
                selectable=False,
                input_schema=tool.input_schema,
            )
        )
    return updated


def collect_catalog_tools(search_payloads: list[tuple[str, dict[str, Any]]]) -> list[CatalogTool]:
    """Dedupe catalog search hits and mark which paid jobs belong on the picker."""
    by_id: dict[str, CatalogTool] = {}
    for hint, payload in search_payloads:
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            continue
        for raw in results:
            if not isinstance(raw, dict):
                continue
            tool = catalog_tool_from_row(raw, hint=hint)
            if tool is None:
                continue
            existing = by_id.get(tool.endpoint_id)
            if existing is None:
                by_id[tool.endpoint_id] = tool
                continue
            # Prefer a hunter/research classification over skip.
            if existing.queue_as == "skip" and tool.queue_as != "skip":
                by_id[tool.endpoint_id] = tool
    return mark_selectable(list(by_id.values()))
