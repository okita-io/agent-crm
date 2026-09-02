"""SEO document loop: scrape target sites and write reviews/plans.

Documents only. This agent never patches live pages, deploys markup, or posts.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from agent_crm.agent_control import stop_if_disabled, wait_while_disabled
from agent_crm.config import get_settings
from agent_crm.enums import (
    ActivityType,
    AgentStatus,
    Brand,
    ImprovementSourceAgent,
    SeoPlanKind,
    SeoQueryKind,
    SeoReviewKind,
    SeoTargetRole,
)
from agent_crm.firecrawl_client import FirecrawlError, scrape
from agent_crm.heartbeat import record_heartbeat
from agent_crm.llm_client import chat_completions
from agent_crm.llm_text import UNTRUSTED_DATA_SYSTEM_SUFFIX, extract_json_object, wrap_untrusted
from agent_crm.marketing_skill import brand_context_snippet
from agent_crm.models import SeoQuery
from agent_crm.searxng_client import SearxngError, search
from .runner import (
    AuditBundle,
    SeoIssue,
    detect_issues,
    extract_page_signals,
    pick_one_thing,
    related_paths_to_fetch,
    score_issues,
)
from .query_store import SeoQueryStore
from .seeds import (
    BRAND_DISPLAY,
    keyword_seeds_for_brand,
    seeds_for_brand,
)
from .skill import plan_writer_guidance, review_writer_guidance
from .store import (
    align_review_schedule,
    earliest_next_review_at,
    get_target,
    list_targets_due,
    mark_target_reviewed,
    next_noon_at,
    review_zone,
    upsert_plan,
    upsert_review,
    upsert_target,
)
from agent_crm.tooling import CRMToolkit
from agent_crm.url_safety import is_public_http_url

ACTOR = "seo"
WATCH_POLL_SECONDS = 60.0

SEO_LOOP_BRANDS: tuple[Brand, ...] = (
    Brand.CELESTIAL_NEXUS,
    Brand.MIDNIGHTSATIN,
    Brand.HEYBUDDY,
    Brand.TACTIC_STUDIO,
)


@dataclass
class SeoBudget:
    max_targets: int = 8
    max_pages_per_target: int = 4
    max_minutes: int = 45

    def __post_init__(self) -> None:
        settings = get_settings()
        if self.max_targets < 0:
            self.max_targets = 0
        if self.max_targets > 0 and settings.seo_max_targets_per_run > 0:
            self.max_targets = min(self.max_targets, settings.seo_max_targets_per_run)
        if self.max_pages_per_target > 0 and settings.seo_max_pages_per_target > 0:
            self.max_pages_per_target = min(
                self.max_pages_per_target, settings.seo_max_pages_per_target
            )


@dataclass
class SeoLoopResult:
    targets_processed: int = 0
    reviews_written: int = 0
    plans_written: int = 0
    pages_scraped: int = 0
    errors: list[str] = field(default_factory=list)
    stop_reason: str = "queue_empty"


def _seed_seo_queue(store: SeoQueryStore, *, brand: Brand | None) -> None:
    align_review_schedule()
    brands = (brand,) if brand is not None else SEO_LOOP_BRANDS
    for cycle_brand in brands:
        for seed in seeds_for_brand(cycle_brand):
            if not is_public_http_url(seed.url, resolve_dns=False):
                continue
            upsert_target(
                url=seed.url,
                brand=cycle_brand,
                role=seed.role,
                title=seed.title,
                notes=seed.notes,
            )
    for target in list_targets_due(brand=brand, limit=200):
        kind = (
            SeoQueryKind.SITE_AUDIT
            if target.role == SeoTargetRole.OWNED
            else SeoQueryKind.COMPETITOR
        )
        store.enqueue_query(
            query=target.url,
            brand=target.brand,
            kind=kind,
            origin=f"seed:{target.role.value}",
            target_id=target.id,
            reopen_completed=True,
        )


def run_seo_loop(
    *,
    brand: Brand | None = None,
    budget: SeoBudget | None = None,
    summarize: bool = True,
    searx_client: httpx.Client | None = None,
    firecrawl_client: httpx.Client | None = None,
) -> SeoLoopResult:
    """Drain the SEO document queue. Writes reviews and plans; never implements."""
    settings = get_settings()
    budget = budget or SeoBudget(
        max_targets=settings.seo_max_targets_per_run,
        max_pages_per_target=settings.seo_max_pages_per_target,
        max_minutes=settings.seo_max_minutes_default,
    )
    crm = CRMToolkit(actor=ACTOR)
    result = SeoLoopResult()
    if stop_if_disabled(ACTOR):
        result.stop_reason = "disabled"
        return result
    deadline = None if budget.max_minutes <= 0 else time.monotonic() + budget.max_minutes * 60
    store = SeoQueryStore()
    store.reset_stale_running_queries(stale_minutes=0)
    _seed_seo_queue(store, brand=brand)

    if store.count_pending(brand=brand) == 0:
        record_heartbeat(ACTOR, status=AgentStatus.IDLE, task="no SEO targets due")
        result.stop_reason = "queue_empty"
        return result

    crm.log_note(
        f"SEO document run started ({store.count_pending(brand=brand)} pending jobs)",
        type=ActivityType.NOTE,
        payload={"brand": brand.value if brand else None},
    )

    queries_run = 0
    brand_cycle = 0
    idle_rounds = 0
    brands = (brand,) if brand is not None else SEO_LOOP_BRANDS

    while True:
        if stop_if_disabled(ACTOR):
            result.stop_reason = "disabled"
            break
        if deadline is not None and time.monotonic() >= deadline:
            result.stop_reason = "max_minutes"
            break
        if budget.max_targets > 0 and queries_run >= budget.max_targets:
            result.stop_reason = "max_targets"
            break

        cycle_brand = brands[brand_cycle % len(brands)]
        brand_cycle += 1
        claimed = store.claim_next_pending_query(brand=cycle_brand)
        if claimed is None:
            idle_rounds += 1
            if idle_rounds >= len(brands):
                result.stop_reason = "queue_empty"
                break
            continue

        idle_rounds = 0
        queries_run += 1
        record_heartbeat(
            ACTOR,
            status=AgentStatus.THINKING,
            task=f"seo {claimed.kind.value}: {claimed.query}",
            resource=settings.firecrawl_url,
        )
        try:
            stats = _run_seo_query(
                claimed,
                budget=budget,
                summarize=summarize,
                searx_client=searx_client,
                firecrawl_client=firecrawl_client,
            )
        except Exception as exc:  # noqa: BLE001
            from agent_crm.agency.orchestrator import note_worker_failure

            message = f"SEO job failed for {claimed.query!r}: {exc}"
            result.errors.append(message)
            note_worker_failure(
                source_agent=ImprovementSourceAgent.SEO_LOOP,
                error_text=str(exc),
                context=f"query {claimed.id}",
            )
            store.mark_query_failed(claimed.id, message)
            continue

        result.reviews_written += stats["reviews"]
        result.plans_written += stats["plans"]
        result.pages_scraped += stats["pages"]
        store.mark_query_completed(claimed.id)
        if claimed.target_id is not None:
            mark_target_reviewed(claimed.target_id)

    result.targets_processed = queries_run
    record_heartbeat(
        ACTOR,
        status=AgentStatus.IDLE,
        task=f"seo loop complete ({result.stop_reason})",
    )
    crm.log_note(
        (
            f"SEO document run finished: {result.reviews_written} reviews, "
            f"{result.plans_written} plans, {result.pages_scraped} pages "
            f"({result.stop_reason})"
        ),
        type=ActivityType.NOTE,
        payload={
            "reviews": result.reviews_written,
            "plans": result.plans_written,
            "pages": result.pages_scraped,
            "stop_reason": result.stop_reason,
        },
    )
    return result


def run_seo_loop_watch(
    *,
    brand: Brand | None = None,
    budget: SeoBudget | None = None,
    summarize: bool = True,
    searx_client: httpx.Client | None = None,
    firecrawl_client: httpx.Client | None = None,
) -> None:
    """Drain due reviews, then idle until the next local noon."""
    while True:
        wait_while_disabled(ACTOR)
        run_seo_loop(
            brand=brand,
            budget=budget,
            summarize=summarize,
            searx_client=searx_client,
            firecrawl_client=firecrawl_client,
        )
        due = list_targets_due(brand=brand, limit=1)
        if due:
            record_heartbeat(
                ACTOR,
                status=AgentStatus.IDLE,
                task="retrying remaining SEO targets",
            )
            time.sleep(WATCH_POLL_SECONDS)
            continue
        nxt = earliest_next_review_at() or next_noon_at()
        _sleep_until(nxt)


def _sleep_until(when: datetime) -> None:
    while True:
        remaining = (_as_aware_utc(when) - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            return
        local = _as_aware_utc(when).astimezone(review_zone())
        record_heartbeat(
            ACTOR,
            status=AgentStatus.IDLE,
            task=f"next SEO pass {local.strftime('%Y-%m-%d %H:%M %Z')}",
        )
        time.sleep(min(WATCH_POLL_SECONDS, remaining))


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _run_seo_query(
    claimed: SeoQuery,
    *,
    budget: SeoBudget,
    summarize: bool,
    searx_client: httpx.Client | None,
    firecrawl_client: httpx.Client | None,
) -> dict[str, int]:
    target = get_target(claimed.target_id) if claimed.target_id else None
    url = (target.url if target is not None else claimed.query).strip()
    if not is_public_http_url(url, resolve_dns=False):
        raise ValueError(f"refusing non-public SEO URL: {url!r}")

    owned = target is None or target.role == SeoTargetRole.OWNED
    bundle = _crawl_target(
        url,
        max_pages=budget.max_pages_per_target,
        firecrawl_client=firecrawl_client,
    )
    keyword_hits = _keyword_serp_notes(
        claimed.brand,
        searx_client=searx_client,
    )
    review_kind = (
        SeoReviewKind.COMPETITOR
        if claimed.kind == SeoQueryKind.COMPETITOR
        else SeoReviewKind.SITE_AUDIT
    )
    one_thing = pick_one_thing(bundle.issues, owned=owned)
    score = bundle.score
    title = f"SEO Review — {target.domain if target else url}"
    body = _heuristic_review_markdown(
        title=title,
        brand=claimed.brand,
        bundle=bundle,
        one_thing=one_thing,
        owned=owned,
        keyword_hits=keyword_hits,
    )
    if summarize:
        written = _llm_review(
            url=url,
            brand=claimed.brand,
            kind=review_kind,
            owned=owned,
            bundle=bundle,
            keyword_hits=keyword_hits,
            fallback_title=title,
            fallback_body=body,
            fallback_one_thing=one_thing,
        )
        if written:
            title, score, one_thing, body = written

    review = upsert_review(
        url=url,
        brand=claimed.brand,
        kind=review_kind,
        title=title[:512],
        body=body,
        target_id=claimed.target_id,
        score=score,
        one_thing=one_thing,
        issues=[issue.as_dict() for issue in bundle.issues],
        evidence=bundle.as_evidence() | {"keyword_hits": keyword_hits},
        source_query=claimed.query,
    )
    stats = {"reviews": 1 if review else 0, "plans": 0, "pages": len(bundle.pages)}
    if review is None or not owned:
        return stats

    plan_title = f"SEO Plan — {target.domain if target else url}"
    tasks = _tasks_from_issues(bundle.issues)
    plan_body = _heuristic_plan_markdown(
        title=plan_title,
        review_title=review.title,
        score=review.score,
        one_thing=one_thing,
        tasks=tasks,
        keyword_hits=keyword_hits,
    )
    if summarize:
        planned = _llm_plan(
            url=url,
            brand=claimed.brand,
            bundle=bundle,
            review_title=review.title,
            review_body=review.body,
            one_thing=one_thing,
            fallback_title=plan_title,
            fallback_body=plan_body,
            fallback_tasks=tasks,
        )
        if planned:
            plan_title, one_thing, plan_body, tasks = planned

    plan = upsert_plan(
        url=url,
        brand=claimed.brand,
        kind=SeoPlanKind.MIXED,
        title=plan_title[:512],
        body=plan_body,
        target_id=claimed.target_id,
        review_id=review.id,
        one_thing=one_thing,
        tasks=tasks,
    )
    stats["plans"] = 1 if plan else 0
    return stats


def _crawl_target(
    url: str,
    *,
    max_pages: int,
    firecrawl_client: httpx.Client | None,
) -> AuditBundle:
    bundle = AuditBundle()
    to_visit = [url]
    seen: set[str] = set()
    while to_visit and len(bundle.pages) < max_pages:
        current = to_visit.pop(0)
        if current in seen or not is_public_http_url(current, resolve_dns=False):
            continue
        seen.add(current)
        try:
            page = scrape(current, client=firecrawl_client)
        except FirecrawlError as exc:
            if not bundle.pages:
                bundle.crawled = False
                bundle.crawl_error = str(exc)[:500]
                bundle.score = 0
                bundle.issues = [
                    SeoIssue(
                        issue_id="blocked-page",
                        severity="critical",
                        title="Crawler could not fetch the page",
                        explanation=(
                            "Firecrawl did not return the page. Search engines may "
                            "face similar friction, or the URL may be wrong."
                        ),
                        how_to_fix=(
                            "Confirm the live URL, allowlist the ranch crawler if you "
                            "own the site, then re-run `agent-crm seo-loop`."
                        ),
                        evidence=str(exc)[:400],
                        url=current,
                    )
                ]
            continue
        signals = extract_page_signals(
            page.url or current,
            markdown=page.markdown,
            metadata=page.metadata,
            title=page.title,
        )
        bundle.pages.append(signals)
        bundle.issues.extend(detect_issues(signals))
        if len(bundle.pages) == 1:
            to_visit.extend(
                href
                for href in related_paths_to_fetch(
                    signals, limit=max(0, max_pages - 1)
                )
                if href not in seen
            )
    if bundle.pages:
        bundle.score = score_issues(bundle.issues)
        bundle.crawled = True
    elif not bundle.issues:
        bundle.crawled = False
        bundle.score = 0
        bundle.crawl_error = bundle.crawl_error or "no pages scraped"
    return bundle


def _keyword_serp_notes(
    brand: Brand, *, searx_client: httpx.Client | None
) -> list[dict[str, str]]:
    settings = get_settings()
    notes: list[dict[str, str]] = []
    for query in keyword_seeds_for_brand(brand)[:3]:
        try:
            hits = search(
                query,
                limit=min(5, settings.seo_search_result_limit),
                client=searx_client,
            )
        except SearxngError:
            continue
        for hit in hits[:3]:
            notes.append(
                {
                    "query": query,
                    "title": hit.title,
                    "url": hit.url,
                    "snippet": (hit.snippet or "")[:240],
                }
            )
    return notes


def _heuristic_review_markdown(
    *,
    title: str,
    brand: Brand,
    bundle: AuditBundle,
    one_thing: str,
    owned: bool,
    keyword_hits: list[dict[str, str]],
) -> str:
    today = datetime.now(UTC).date().isoformat()
    display = BRAND_DISPLAY.get(brand, brand.value)
    lines = [
        f"# {title}",
        (
            f"{today} · Score: {bundle.score}/100 · Basis: Firecrawl page signals "
            f"+ SearXNG (no rank/backlink vendor) · Brand: {display}"
        ),
        "",
        "## The one thing",
        one_thing,
        "",
        "## Scorecard",
        (
            f"Heuristic score {bundle.score}/100 from {len(bundle.issues)} issue(s) "
            f"across {len(bundle.pages)} page(s). This is not a Semrush/Ahrefs rank."
        ),
        "",
        "## Small fixes",
    ]
    if bundle.issues:
        for issue in bundle.issues[:10]:
            lines.append(
                f"- **{issue.title}** ({issue.severity}) on `{issue.url}` — "
                f"evidence: {issue.evidence}. {issue.how_to_fix}"
            )
    else:
        lines.append("- No blocking technical issues from the crawl.")
    lines.extend(["", "## Already working"])
    working = _already_working(bundle)
    lines.extend(f"- {item}" for item in working)
    lines.extend(["", "## Where to focus first"])
    if owned and keyword_hits:
        lines.append("Seed queries and live SERP titles (not volumes):")
        for hit in keyword_hits[:8]:
            lines.append(f"- `{hit['query']}` → {hit['title']} ({hit['url']})")
    elif owned:
        lines.append(
            "[NEED: SearXNG keyword hits] Seed topics from brand context; "
            "do not invent search volume."
        )
    else:
        lines.append("Competitor review — no implementation plan will be written.")
    lines.extend(
        [
            "",
            "## What I couldn't determine",
            (
                "- Organic traffic, keyword difficulty, and backlinks: "
                "[NEED: Search Console / vendor crawl]."
            ),
            "- Core Web Vitals / Lighthouse: not run.",
            "- JavaScript-only content may be missing from Firecrawl markdown.",
        ]
    )
    if bundle.crawl_error:
        lines.append(f"- Crawl error: {bundle.crawl_error}")
    lines.extend(
        [
            "",
            "## Method",
            (
                "Local Firecrawl scrape plus a heuristic issue engine adapted from "
                "OpenSEO site-audit types. SearXNG used only for keyword SERP titles. "
                "This stack does not implement the fixes."
            ),
        ]
    )
    return "\n".join(lines)


def _heuristic_plan_markdown(
    *,
    title: str,
    review_title: str,
    score: int | None,
    one_thing: str,
    tasks: list[dict[str, Any]],
    keyword_hits: list[dict[str, str]],
) -> str:
    lines = [
        f"# {title}",
        f"Derived from review: {review_title}"
        + (f" · Score {score}/100" if score is not None else ""),
        "",
        "## Do not implement from this agent",
        "This document is for humans. The CRM will not change the live site.",
        "",
        "## The one thing this week",
        one_thing,
        "",
        "## Implementation tasks",
    ]
    if tasks:
        for task in tasks:
            lines.append(
                f"1. **{task.get('task')}** ({task.get('effort', 'S')}) on "
                f"`{task.get('page')}` — {task.get('implement')} "
                f"Verify: {task.get('verify')}"
            )
    else:
        lines.append("- No automated tasks. Use the review's one thing as the brief.")
    lines.extend(["", "## Keyword pages to create (if any)"])
    if keyword_hits:
        seen: set[str] = set()
        for hit in keyword_hits:
            query = hit.get("query") or ""
            if query in seen:
                continue
            seen.add(query)
            lines.append(
                f"- Consider a page that honestly answers `{query}` "
                f"(SERP currently shows: {hit.get('title')}). Do not copy competitors."
            )
    else:
        lines.append("- [NEED: keyword SERP notes]")
    lines.extend(
        [
            "",
            "## Out of scope / [NEED]",
            "- Rank tracking, backlink outreach, and paid ads are out of scope.",
            "- tactic.studio outbound remains gated by Pete + naming-rights.",
        ]
    )
    return "\n".join(lines)


def _tasks_from_issues(issues: list[SeoIssue]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for index, issue in enumerate(issues[:8], start=1):
        effort = {"critical": "S", "high": "S", "medium": "S", "low": "S"}.get(
            issue.severity, "S"
        )
        if issue.issue_id in {"thin-content"}:
            effort = "M"
        tasks.append(
            {
                "priority": index,
                "effort": effort,
                "page": issue.url,
                "task": issue.title,
                "implement": issue.how_to_fix,
                "verify": "Fetch the live URL and confirm the evidence string is gone.",
            }
        )
    return tasks


def _already_working(bundle: AuditBundle) -> list[str]:
    notes: list[str] = []
    for page in bundle.pages:
        if page.title:
            notes.append(f"Title present on {page.url}: {page.title}")
        if page.h1_count == 1 and page.h1_text:
            notes.append(f"Single H1 on {page.url}: {page.h1_text[0]}")
        if page.has_json_ld:
            notes.append(f"Structured data hints on {page.url}")
        if page.word_count >= 400:
            notes.append(f"Substantial copy on {page.url} (~{page.word_count} words)")
        if len(notes) >= 4:
            break
    if not notes:
        notes.append("Crawl completed; see gaps in the scorecard rather than padding praise.")
    return notes[:5]


def _llm_review(
    *,
    url: str,
    brand: Brand,
    kind: SeoReviewKind,
    owned: bool,
    bundle: AuditBundle,
    keyword_hits: list[dict[str, str]],
    fallback_title: str,
    fallback_body: str,
    fallback_one_thing: str,
) -> tuple[str, int, str, str] | None:
    settings = get_settings()
    brand_context = brand_context_snippet(brand, max_chars=700)
    system = (
        review_writer_guidance()
        + " You output JSON only."
        + UNTRUSTED_DATA_SYSTEM_SUFFIX
    )
    if brand_context:
        system += f"\n\n--- brand context (excerpt) ---\n{brand_context}"
    register = "owned site (write a fix-list)" if owned else "competitor (no implementation plan)"
    user = (
        f"Brand: {brand.value}\nRegister: {register}\nKind: {kind.value}\n"
        f"{wrap_untrusted('url', url, max_chars=400)}\n"
        f"{wrap_untrusted('issues', json.dumps([i.as_dict() for i in bundle.issues])[:4000], max_chars=4000)}\n"
        f"{wrap_untrusted('signals', json.dumps(bundle.as_evidence())[:3500], max_chars=3500)}\n"
        f"{wrap_untrusted('keyword_hits', json.dumps(keyword_hits)[:2000], max_chars=2000)}\n"
        f"{wrap_untrusted('heuristic_draft', fallback_body, max_chars=2500)}\n\n"
        "Return JSON: {\"title\": \"...\", \"score\": 0, \"one_thing\": \"...\", "
        "\"body\": \"markdown\"}. Score must stay a heuristic 0-100."
    )
    try:
        record_heartbeat(
            ACTOR,
            status=AgentStatus.WORKING,
            task=f"writing SEO review for {url}",
            resource=f"Spark queue ({settings.llm_base_url})",
        )
        response = chat_completions(
            {
                "model": "crm",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 1400,
                "temperature": 0.2,
            },
            timeout=180.0,
            actor=ACTOR,
            task=f"seo review {url[:40]}",
        )
        parsed = extract_json_object(_extract_chat_content(response) or "")
    except Exception:  # noqa: BLE001
        return None
    if not parsed:
        return None
    title = str(parsed.get("title") or fallback_title).strip() or fallback_title
    body = str(parsed.get("body") or fallback_body).strip() or fallback_body
    one_thing = str(parsed.get("one_thing") or fallback_one_thing).strip() or fallback_one_thing
    score = parsed.get("score", bundle.score)
    try:
        score_int = int(score)
    except (TypeError, ValueError):
        score_int = bundle.score
    score_int = max(0, min(100, score_int))
    return title, score_int, one_thing, body


def _llm_plan(
    *,
    url: str,
    brand: Brand,
    bundle: AuditBundle,
    review_title: str,
    review_body: str,
    one_thing: str,
    fallback_title: str,
    fallback_body: str,
    fallback_tasks: list[dict[str, Any]],
) -> tuple[str, str, str, list[dict[str, Any]]] | None:
    settings = get_settings()
    brand_context = brand_context_snippet(brand, max_chars=600)
    system = (
        plan_writer_guidance()
        + " You output JSON only."
        + UNTRUSTED_DATA_SYSTEM_SUFFIX
    )
    if brand_context:
        system += f"\n\n--- brand context (excerpt) ---\n{brand_context}"
    user = (
        f"Brand: {brand.value}\nOwned URL: {url}\nReview: {review_title}\n"
        f"{wrap_untrusted('review', review_body, max_chars=2500)}\n"
        f"{wrap_untrusted('issues', json.dumps([i.as_dict() for i in bundle.issues])[:3000], max_chars=3000)}\n"
        f"{wrap_untrusted('heuristic_plan', fallback_body, max_chars=2000)}\n\n"
        "Return JSON: {\"title\": \"...\", \"one_thing\": \"...\", \"body\": \"markdown\", "
        "\"tasks\": [{\"priority\": 1, \"effort\": \"S\", \"page\": \"...\", "
        "\"task\": \"...\", \"implement\": \"...\", \"verify\": \"...\"}]}"
    )
    try:
        record_heartbeat(
            ACTOR,
            status=AgentStatus.WORKING,
            task=f"writing SEO plan for {url}",
            resource=f"Spark queue ({settings.llm_base_url})",
        )
        response = chat_completions(
            {
                "model": "crm",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 1400,
                "temperature": 0.2,
            },
            timeout=180.0,
            actor=ACTOR,
            task=f"seo plan {url[:40]}",
        )
        parsed = extract_json_object(_extract_chat_content(response) or "")
    except Exception:  # noqa: BLE001
        return None
    if not parsed:
        return None
    title = str(parsed.get("title") or fallback_title).strip() or fallback_title
    body = str(parsed.get("body") or fallback_body).strip() or fallback_body
    thing = str(parsed.get("one_thing") or one_thing).strip() or one_thing
    tasks = parsed.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        tasks = fallback_tasks
    cleaned: list[dict[str, Any]] = []
    for item in tasks[:12]:
        if not isinstance(item, dict):
            continue
        cleaned.append(
            {
                "priority": item.get("priority") or len(cleaned) + 1,
                "effort": str(item.get("effort") or "S")[:8],
                "page": str(item.get("page") or url)[:2048],
                "task": str(item.get("task") or "")[:300],
                "implement": str(item.get("implement") or "")[:2000],
                "verify": str(item.get("verify") or "")[:500],
            }
        )
    return title, thing, body, cleaned or fallback_tasks


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
