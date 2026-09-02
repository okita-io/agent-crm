"""Retail / F&B companies extracted by research → hunter people searches."""

from __future__ import annotations

import re
from typing import Any

from agent_crm.enums import Brand, ContactAudience
from agent_crm.hunt.feedback import enqueue_company_people_terms
from agent_crm.hunt.store import HuntStore
from agent_crm.hunt.utils import normalize_query

MAX_COMPANIES_PER_PAGE = 25
MAX_HUNT_TERMS_PER_COMPANY = 4

_LISTICLE_TITLE_RE = re.compile(
    r"\b(top|largest|best|list of|ranking|biggest)\b",
    re.IGNORECASE,
)
_SKIP_NAMES = frozenset(
    {
        "home",
        "about",
        "contact",
        "wikipedia",
        "linkedin",
        "official site",
        "united states",
        "usa",
        "u.s.",
        "top 10",
        "top 50",
        "top 100",
        "list",
        "index",
        "news",
        "press",
        "blog",
    }
)
_COMPANY_STRIP_RE = re.compile(
    r"\s*[-–|]\s*(official site|home|wikipedia|linkedin|about).*$",
    re.IGNORECASE,
)


def clean_company_name(value: str | None) -> str | None:
    """Return a searchable company name, or None when the value is junk."""
    if not value or not isinstance(value, str):
        return None
    cleaned = _COMPANY_STRIP_RE.sub("", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,-")
    if len(cleaned) < 2 or len(cleaned) > 80:
        return None
    if "@" in cleaned or "http" in cleaned.lower():
        return None
    if cleaned.lower() in _SKIP_NAMES:
        return None
    if _LISTICLE_TITLE_RE.search(cleaned) and len(cleaned.split()) > 6:
        return None
    return cleaned


def companies_from_payload(parsed: dict[str, Any] | None) -> list[dict[str, str]]:
    """Normalize a Spark JSON payload into a bounded company list."""
    if not parsed:
        return []
    raw = parsed.get("companies")
    rows: list[Any] = []
    if isinstance(raw, list):
        rows = raw
    elif isinstance(parsed.get("name"), str):
        rows = [parsed]
    elif isinstance(parsed.get("org_name"), str):
        rows = [{"name": parsed["org_name"], "sector": parsed.get("sector")}]

    companies: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in rows:
        if isinstance(item, str):
            name = clean_company_name(item)
            sector = ""
            revenue_hint = ""
            why_target = ""
        elif isinstance(item, dict):
            name = clean_company_name(
                item.get("name") if isinstance(item.get("name"), str) else None
            )
            sector = str(item.get("sector") or "").strip()[:80]
            revenue_hint = str(item.get("revenue_hint") or "").strip()[:120]
            why_target = str(item.get("why_target") or "").strip()[:240]
        else:
            continue
        if not name:
            continue
        key = normalize_query(name)
        if key in seen:
            continue
        seen.add(key)
        entry = {"name": name}
        if sector:
            entry["sector"] = sector
        if revenue_hint:
            entry["revenue_hint"] = revenue_hint
        if why_target:
            entry["why_target"] = why_target
        companies.append(entry)
        if len(companies) >= MAX_COMPANIES_PER_PAGE:
            break
    return companies


def companies_from_extra(extra: dict[str, Any] | None) -> list[dict[str, str]]:
    """Read a previously normalized extra blob."""
    if not extra:
        return []
    return companies_from_payload(extra)


def heuristic_companies_from_title(title: str | None) -> list[dict[str, str]]:
    """Use a page title as a single company when it is not a listicle."""
    name = clean_company_name(title)
    if not name:
        return []
    if _LISTICLE_TITLE_RE.search(name):
        return []
    return [{"name": name, "sector": "retail"}]


def enqueue_target_company_hunts(
    *,
    extra: dict[str, Any] | None,
    brand: Brand,
    run_id: str | None = None,
) -> int:
    """Enqueue per-company marketing/sales people searches for the hunter."""
    companies = companies_from_extra(extra)
    if not companies:
        return 0
    store = HuntStore()
    enqueued = 0
    for company in companies:
        enqueued += enqueue_company_people_terms(
            store,
            company=company["name"],
            brand=brand,
            run_id=run_id,
            audience=ContactAudience.MARKETING,
            max_terms=MAX_HUNT_TERMS_PER_COMPANY,
        )
    return enqueued
