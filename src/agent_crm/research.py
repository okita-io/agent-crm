"""Research agent: competitor and nonprofit prospecting via SearXNG + Firecrawl + Spark."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import get_settings
from .contact_store import ContactExtractionBudget, process_scraped_page_contacts
from .enums import ActivityType, AgentStatus, Brand, ResearchFindingKind
from .firecrawl_client import FirecrawlError, ScrapeResult, scrape
from .llm_client import chat_completions
from .research_seeds import BRAND_DISPLAY, default_kind_for_brand, seed_queries
from .research_store import upsert_finding
from .research_utils import (
    canonical_url,
    clean_title,
    extract_domain,
    extract_ein_from_text,
    is_junk_finding,
    is_scrapable_url,
)
from .schemas import ResearchRequest, ResearchResult
from .searxng_client import SearchResult, SearxngError, search
from .tooling import CRMToolkit

ACTOR = "research"


@dataclass(frozen=True)
class ResearchBudget:
    max_queries: int
    max_pages: int
    max_minutes: int


def run_research(
    request: ResearchRequest,
    *,
    searx_client: httpx.Client | None = None,
    firecrawl_client: httpx.Client | None = None,
) -> ResearchResult:
    """Execute a bounded research run for one brand/kind."""
    settings = get_settings()
    crm = CRMToolkit(actor=ACTOR)

    kind = request.kind or default_kind_for_brand(request.brand)
    budget = ResearchBudget(
        max_queries=min(request.max_queries, settings.research_max_queries_default),
        max_pages=min(request.max_pages, settings.research_max_pages_per_run),
        max_minutes=min(request.max_minutes, settings.research_max_minutes_default),
    )
    search_limit = min(request.search_limit, settings.research_search_result_limit)

    queries = seed_queries(request.brand, kind, explicit_query=request.query)
    queries = queries[: budget.max_queries]

    started = time.monotonic()
    errors: list[str] = []
    findings_written: list[int] = []
    queries_run = 0
    pages_scraped = 0
    seen_urls: set[str] = set()
    contact_budget = ContactExtractionBudget.from_settings()

    crm.log_note(
        f"Research run started for {request.brand.value} ({kind.value})",
        type=ActivityType.NOTE,
        payload={"brand": request.brand.value, "kind": kind.value, "queries": len(queries)},
    )

    for query in queries:
        if queries_run >= budget.max_queries:
            break
        if _elapsed_minutes(started) >= budget.max_minutes:
            break
        if pages_scraped >= budget.max_pages:
            break

        queries_run += 1
        crm.record_heartbeat(
            status=AgentStatus.THINKING,
            task=f"searching: {query}",
            resource=settings.searxng_url,
        )

        try:
            results = search(query, limit=search_limit, client=searx_client)
        except SearxngError as exc:
            message = f"SearXNG search failed for {query!r}: {exc}"
            errors.append(message)
            crm.log_note(message, type=ActivityType.ERROR, payload={"query": query})
            continue

        for hit in results:
            if pages_scraped >= budget.max_pages:
                break
            if _elapsed_minutes(started) >= budget.max_minutes:
                break

            normalized = canonical_url(hit.url)
            if normalized in seen_urls:
                continue
            if not is_scrapable_url(normalized):
                continue
            if is_junk_finding(title=hit.title, snippet=hit.snippet):
                continue

            seen_urls.add(normalized)

            crm.record_heartbeat(
                status=AgentStatus.WORKING,
                task=f"scraping {normalized}",
                resource=settings.firecrawl_url,
            )

            try:
                page = scrape(normalized, client=firecrawl_client)
            except FirecrawlError as exc:
                message = f"Firecrawl scrape failed for {normalized}: {exc}"
                errors.append(message)
                crm.log_note(message, type=ActivityType.ERROR, payload={"url": normalized})
                continue

            title = clean_title(page.title or hit.title or extract_domain(normalized))
            if is_junk_finding(title=title, snippet=hit.snippet, markdown=page.markdown):
                continue

            pages_scraped += 1
            fallback = _fallback_summary(hit, page, kind, request.brand)
            summary = fallback
            extra: dict[str, Any] | None = None

            if request.summarize:
                crm.record_heartbeat(
                    status=AgentStatus.WORKING,
                    task=f"summarizing {normalized}",
                    resource=f"Spark queue ({settings.llm_base_url})",
                )
                summary, extra = _maybe_summarize(
                    hit,
                    page,
                    fallback,
                    brand=request.brand,
                    kind=kind,
                    errors=errors,
                )

            if extra is None:
                extra = _heuristic_extra(page, hit, kind)

            finding = upsert_finding(
                url=normalized,
                title=title,
                brand=request.brand,
                kind=kind,
                summary=summary,
                source_query=query,
                raw_snippet=hit.snippet or None,
                extra=extra,
            )
            findings_written.append(finding.id)

            if request.write_accounts and _is_strong_hit(summary, extra):
                _maybe_write_account_note(crm, normalized, title, summary, extra)

            try:
                process_scraped_page_contacts(
                    markdown=page.markdown,
                    source_url=normalized,
                    brand=request.brand,
                    searx_client=searx_client,
                    budget=contact_budget,
                )
            except Exception:  # noqa: BLE001
                pass

    crm.record_heartbeat(status=AgentStatus.IDLE)
    return ResearchResult(
        brand=request.brand,
        kind=kind,
        queries_run=queries_run,
        pages_scraped=pages_scraped,
        findings_written=findings_written,
        errors=errors,
    )


def _elapsed_minutes(started: float) -> float:
    return (time.monotonic() - started) / 60.0


def _fallback_summary(
    hit: SearchResult,
    page: ScrapeResult,
    kind: ResearchFindingKind,
    brand: Brand,
) -> str:
    brand_label = BRAND_DISPLAY.get(brand, brand.value)
    title = page.title or hit.title or hit.url
    body = (page.markdown or "").strip()[:1200]
    if kind == ResearchFindingKind.COMPETITOR:
        prefix = f"Competitor site vs {brand_label}: "
    elif kind == ResearchFindingKind.NONPROFIT:
        prefix = "Potential nonprofit / 501(c)(3) partner for HeyBuddy: "
    else:
        prefix = "Research finding: "
    parts = [prefix + title]
    if hit.snippet:
        parts.append(hit.snippet)
    if body:
        parts.append(body)
    return "\n\n".join(parts).strip()


def _maybe_summarize(
    hit: SearchResult,
    page: ScrapeResult,
    fallback: str,
    *,
    brand: Brand,
    kind: ResearchFindingKind,
    errors: list[str],
) -> tuple[str, dict[str, Any] | None]:
    brand_label = BRAND_DISPLAY.get(brand, brand.value)
    if kind == ResearchFindingKind.COMPETITOR:
        system = (
            "You analyze competitor websites for a CRM research agent. "
            "Summarize positioning, audience, and product angle vs the target brand. "
            "Be factual; do not invent contact details."
        )
        user = (
            f"Target brand: {brand_label}\n"
            f"URL: {hit.url}\n"
            f"Title: {page.title or hit.title}\n"
            f"Snippet: {hit.snippet}\n"
            f"Page excerpt:\n{(page.markdown or '')[:3500]}\n\n"
            'Return JSON: {"summary": "...", "why_it_matters": "..."}'
        )
    elif kind == ResearchFindingKind.NONPROFIT:
        system = (
            "You analyze US nonprofits and 501(c)(3)-adjacent organizations for partnership "
            "prospecting with an AI companion app (HeyBuddy). Focus on mission overlap: "
            "loneliness, mental wellness, elder companionship, veterans, youth digital wellbeing. "
            "Only include ein if it appears verbatim in the source text; never invent tax status."
        )
        user = (
            f"URL: {hit.url}\n"
            f"Title: {page.title or hit.title}\n"
            f"Snippet: {hit.snippet}\n"
            f"Page excerpt:\n{(page.markdown or '')[:3500]}\n\n"
            'Return JSON: {"summary": "...", "org_name": "...", "mission": "...", '
            '"ein": null or "XX-XXXXXXX", "why_it_matters": "..."}'
        )
    else:
        system = "You write concise CRM research summaries. Be factual."
        user = (
            f"URL: {hit.url}\nTitle: {page.title or hit.title}\n"
            f"Snippet: {hit.snippet}\nPage:\n{(page.markdown or '')[:3500]}"
        )

    try:
        response = chat_completions(
            {
                "model": "crm",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 320,
                "temperature": 0.2,
            },
            timeout=120.0,
            actor=ACTOR,
            task=f"summarize {hit.url}",
        )
        content = _extract_chat_content(response)
        if not content:
            return fallback, _heuristic_extra(page, hit, kind)

        parsed = _parse_json_object(content)
        if parsed:
            summary = str(parsed.get("summary") or fallback).strip()
            extra = _normalize_extra(parsed, page, hit, kind)
            return summary, extra
        if content.strip():
            return content.strip(), _heuristic_extra(page, hit, kind)
    except Exception as exc:  # noqa: BLE001 — research must not block on LLM failure
        errors.append(f"LLM summary failed for {hit.url}: {exc}")

    return fallback, _heuristic_extra(page, hit, kind)


def _heuristic_extra(
    page: ScrapeResult,
    hit: SearchResult,
    kind: ResearchFindingKind,
) -> dict[str, Any] | None:
    if kind != ResearchFindingKind.NONPROFIT:
        return None
    text = " ".join(
        part
        for part in (
            page.title or "",
            hit.title or "",
            hit.snippet or "",
            (page.markdown or "")[:4000],
        )
        if part
    )
    ein = extract_ein_from_text(text)
    extra: dict[str, Any] = {}
    if ein:
        extra["ein"] = ein
    org_name = clean_title(page.title or hit.title or "")
    if org_name:
        extra["org_name"] = org_name
    return extra or None


def _normalize_extra(
    parsed: dict[str, Any],
    page: ScrapeResult,
    hit: SearchResult,
    kind: ResearchFindingKind,
) -> dict[str, Any] | None:
    extra: dict[str, Any] = {}
    for key in ("org_name", "mission", "why_it_matters"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            extra[key] = value.strip()

    if kind == ResearchFindingKind.NONPROFIT:
        text = " ".join(
            part
            for part in (
                page.title or "",
                hit.snippet or "",
                (page.markdown or "")[:4000],
            )
            if part
        )
        source_ein = extract_ein_from_text(text)
        if source_ein:
            extra["ein"] = source_ein

    return extra or None


def _parse_json_object(content: str) -> dict[str, Any] | None:
    content = content.strip()
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
    return None


def _extract_chat_content(response: dict[str, Any]) -> str | None:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None


def _is_strong_hit(summary: str, extra: dict[str, Any] | None) -> bool:
    if len(summary) >= 120:
        return True
    if extra and any(extra.get(key) for key in ("ein", "mission", "why_it_matters")):
        return True
    return False


def _maybe_write_account_note(
    crm: CRMToolkit,
    url: str,
    title: str,
    summary: str,
    extra: dict[str, Any] | None,
) -> None:
    """Optional pipeline visibility: account note + activity without fake emails."""
    from .db import session_scope
    from .models import Account

    domain = extract_domain(url)
    org_name = (extra or {}).get("org_name") if extra else None
    account_name = org_name or title or domain

    with session_scope() as session:
        account = Account(name=str(account_name)[:255], website=url)
        note_parts = [summary[:2000]]
        if extra:
            if extra.get("mission"):
                note_parts.append(f"Mission: {extra['mission']}")
            if extra.get("ein"):
                note_parts.append(f"EIN: {extra['ein']}")
            if extra.get("why_it_matters"):
                note_parts.append(f"Why it matters: {extra['why_it_matters']}")
        account.notes = "\n\n".join(note_parts)
        session.add(account)
        session.flush()

    crm.log_note(
        f"Research hit recorded for {domain}",
        type=ActivityType.NOTE,
        payload={"url": url, "account_name": account_name, "extra": extra},
    )
