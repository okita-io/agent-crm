"""AEO/GEO document loop: scrape target sites and write reviews/plans.

Documents only. This agent never patches live pages, robots.txt, or markup.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from agent_crm.agent_control import stop_if_disabled, wait_while_disabled
from .runner import (
    AeoGeoBundle,
    AeoGeoIssue,
    detect_aeo_geo_issues,
    extract_extractability_signals,
    pick_one_aeo_geo_thing,
    related_paths_for_aeo_geo,
    score_aeo_geo_issues,
)
from .seeds import prompt_panel_seeds_for_brand
from .skill import plan_writer_guidance, review_writer_guidance
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
from agent_crm.models import SeoQuery
from agent_crm.seo.runner import extract_page_signals
from agent_crm.seo.loop import SeoBudget, _sleep_until
from agent_crm.seo.query_store import SeoQueryStore
from agent_crm.seo.seeds import BRAND_DISPLAY, seeds_for_brand
from agent_crm.seo.store import (
    align_review_schedule,
    earliest_next_review_at,
    get_target,
    list_targets_due,
    mark_target_reviewed,
    next_noon_at,
    upsert_plan,
    upsert_review,
    upsert_target,
)
from agent_crm.skill_runtime import brand_context_for, has_skill
from agent_crm.tooling import CRMToolkit
from agent_crm.url_safety import is_public_http_url

ACTOR = "aeo-geo"
WATCH_POLL_SECONDS = 60.0

AEO_GEO_LOOP_BRANDS: tuple[Brand, ...] = (
    Brand.CELESTIAL_NEXUS,
    Brand.MIDNIGHTSATIN,
    Brand.HEYBUDDY,
    Brand.TACTIC_STUDIO,
)


def aeo_geo_loop_brands() -> tuple[Brand, ...]:
    from agent_crm.projects.channel_flags import active_brands_for

    return active_brands_for("aeo_geo") or AEO_GEO_LOOP_BRANDS


@dataclass
class AeoGeoLoopResult:
    targets_processed: int = 0
    reviews_written: int = 0
    plans_written: int = 0
    pages_scraped: int = 0
    errors: list[str] = field(default_factory=list)
    stop_reason: str = "queue_empty"


def _seed_aeo_geo_queue(store: SeoQueryStore, *, brand: Brand | None) -> None:
    align_review_schedule()
    brands = (brand,) if brand is not None else aeo_geo_loop_brands()
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
        store.enqueue_query(
            query=target.url,
            brand=target.brand,
            kind=SeoQueryKind.AEO_GEO,
            origin=f"aeo-geo:{target.role.value}",
            target_id=target.id,
            reopen_completed=True,
        )


def run_aeo_geo_loop(
    *,
    brand: Brand | None = None,
    budget: SeoBudget | None = None,
    summarize: bool = True,
    firecrawl_client: httpx.Client | None = None,
) -> AeoGeoLoopResult:
    """Drain the AEO/GEO document queue. Writes reviews and plans; never implements."""
    settings = get_settings()
    budget = budget or SeoBudget(
        max_targets=settings.seo_max_targets_per_run,
        max_pages_per_target=settings.seo_max_pages_per_target,
        max_minutes=settings.seo_max_minutes_default,
    )
    crm = CRMToolkit(actor=ACTOR)
    result = AeoGeoLoopResult()
    if stop_if_disabled(ACTOR):
        result.stop_reason = "disabled"
        return result
    deadline = None if budget.max_minutes <= 0 else time.monotonic() + budget.max_minutes * 60
    store = SeoQueryStore()
    store.reset_stale_running_queries(stale_minutes=0)
    _seed_aeo_geo_queue(store, brand=brand)

    pending = store.count_pending(brand=brand)
    aeo_geo_pending = _count_aeo_geo_pending(store, brand=brand)
    if aeo_geo_pending == 0:
        record_heartbeat(ACTOR, status=AgentStatus.IDLE, task="no AEO/GEO targets due")
        result.stop_reason = "queue_empty"
        return result

    crm.log_note(
        f"AEO/GEO document run started ({aeo_geo_pending} pending jobs of {pending} total)",
        type=ActivityType.NOTE,
        payload={"brand": brand.value if brand else None},
    )

    queries_run = 0
    brand_cycle = 0
    idle_rounds = 0
    brands = (brand,) if brand is not None else aeo_geo_loop_brands()

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
        claimed = _claim_aeo_geo_query(store, brand=cycle_brand)
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
            task=f"aeo/geo {claimed.kind.value}: {claimed.query}",
            resource=settings.firecrawl_url,
        )
        try:
            stats = _run_aeo_geo_query(
                claimed,
                budget=budget,
                summarize=summarize,
                firecrawl_client=firecrawl_client,
            )
        except Exception as exc:  # noqa: BLE001
            from agent_crm.agency.orchestrator import note_worker_failure

            message = f"AEO/GEO job failed for {claimed.query!r}: {exc}"
            result.errors.append(message)
            note_worker_failure(
                source_agent=ImprovementSourceAgent.AEO_GEO_LOOP,
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
        task=f"aeo/geo loop complete ({result.stop_reason})",
    )
    crm.log_note(
        (
            f"AEO/GEO document run finished: {result.reviews_written} reviews, "
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


def run_aeo_geo_loop_watch(
    *,
    brand: Brand | None = None,
    budget: SeoBudget | None = None,
    summarize: bool = True,
    firecrawl_client: httpx.Client | None = None,
) -> None:
    """Drain due AEO/GEO reviews, then idle until the next local noon."""
    while True:
        wait_while_disabled(ACTOR)
        run_aeo_geo_loop(
            brand=brand,
            budget=budget,
            summarize=summarize,
            firecrawl_client=firecrawl_client,
        )
        due = list_targets_due(brand=brand, limit=1)
        if due:
            record_heartbeat(
                ACTOR,
                status=AgentStatus.IDLE,
                task="retrying remaining AEO/GEO targets",
            )
            time.sleep(WATCH_POLL_SECONDS)
            continue
        nxt = earliest_next_review_at() or next_noon_at()
        _sleep_until(nxt)


def _count_aeo_geo_pending(store: SeoQueryStore, *, brand: Brand | None) -> int:
    from sqlalchemy import func, select

    from agent_crm.db import session_scope
    from agent_crm.enums import SeoQueryStatus
    from agent_crm.models import SeoQuery

    with session_scope() as session:
        stmt = (
            select(func.count())
            .select_from(SeoQuery)
            .where(
                SeoQuery.status == SeoQueryStatus.PENDING,
                SeoQuery.kind == SeoQueryKind.AEO_GEO,
            )
        )
        if brand is not None:
            stmt = stmt.where(SeoQuery.brand == brand)
        return session.scalar(stmt) or 0


def _claim_aeo_geo_query(store: SeoQueryStore, *, brand: Brand) -> SeoQuery | None:
    from sqlalchemy import select

    from agent_crm.db import session_scope, with_row_lock
    from agent_crm.enums import SeoQueryStatus
    from agent_crm.models import SeoQuery

    with session_scope() as session:
        stmt = (
            select(SeoQuery)
            .where(
                SeoQuery.status == SeoQueryStatus.PENDING,
                SeoQuery.kind == SeoQueryKind.AEO_GEO,
                SeoQuery.brand == brand,
            )
            .order_by(SeoQuery.id.asc())
            .limit(1)
        )
        row = session.scalar(with_row_lock(stmt, session))
        if row is None:
            return None
        row.status = SeoQueryStatus.RUNNING
        session.flush()
        return row


def _run_aeo_geo_query(
    claimed: SeoQuery,
    *,
    budget: SeoBudget,
    summarize: bool,
    firecrawl_client: httpx.Client | None,
) -> dict[str, int]:
    target = get_target(claimed.target_id) if claimed.target_id else None
    url = (target.url if target is not None else claimed.query).strip()
    if not is_public_http_url(url, resolve_dns=False):
        raise ValueError(f"refusing non-public AEO/GEO URL: {url!r}")

    owned = target is None or target.role == SeoTargetRole.OWNED
    bundle = _crawl_target(url, max_pages=budget.max_pages_per_target, firecrawl_client=firecrawl_client)
    prompt_seeds = list(prompt_panel_seeds_for_brand(claimed.brand))
    one_thing = pick_one_aeo_geo_thing(bundle.issues, owned=owned)
    score = bundle.score
    domain = target.domain if target else url
    title = f"AEO/GEO Review — {domain}"
    body = _heuristic_review_markdown(
        title=title,
        brand=claimed.brand,
        bundle=bundle,
        one_thing=one_thing,
        owned=owned,
        prompt_seeds=prompt_seeds,
    )
    if summarize:
        written = _llm_review(
            url=url,
            brand=claimed.brand,
            owned=owned,
            bundle=bundle,
            prompt_seeds=prompt_seeds,
            fallback_title=title,
            fallback_body=body,
            fallback_one_thing=one_thing,
        )
        if written:
            title, score, one_thing, body = written

    review = upsert_review(
        url=url,
        brand=claimed.brand,
        kind=SeoReviewKind.GEO,
        title=title[:512],
        body=body,
        target_id=claimed.target_id,
        score=score,
        one_thing=one_thing,
        issues=[issue.as_dict() for issue in bundle.issues],
        evidence=bundle.as_evidence() | {"prompt_panel_seeds": prompt_seeds},
        source_query=claimed.query,
    )
    stats = {"reviews": 1 if review else 0, "plans": 0, "pages": len(bundle.pages)}
    if review is None or not owned:
        return stats

    plan_title = f"AEO/GEO Plan — {domain}"
    tasks = _tasks_from_issues(bundle.issues, prompt_seeds=prompt_seeds)
    plan_body = _heuristic_plan_markdown(
        title=plan_title,
        review_title=review.title,
        score=review.score,
        one_thing=one_thing,
        tasks=tasks,
        prompt_seeds=prompt_seeds,
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
        kind=SeoPlanKind.GEO,
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
) -> AeoGeoBundle:
    bundle = AeoGeoBundle()
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
                    AeoGeoIssue(
                        issue_id="blocked-page",
                        severity="critical",
                        title="Crawler could not fetch the page",
                        explanation=(
                            "Firecrawl did not return the page. Search and chat crawlers "
                            "may face similar friction, or the URL may be wrong."
                        ),
                        how_to_fix=(
                            "Confirm the live URL, allow Googlebot and OAI-SearchBot if you "
                            "own the site, then re-run `agent-crm aeo-geo-loop`."
                        ),
                        evidence=str(exc)[:400],
                        url=current,
                        lever="access",
                    )
                ]
            continue
        seo_signals = extract_page_signals(
            page.url or current,
            markdown=page.markdown,
            metadata=page.metadata,
            title=page.title,
        )
        signals = extract_extractability_signals(
            page.url or current,
            markdown=page.markdown or "",
            metadata=page.metadata,
            title=page.title,
        )
        bundle.pages.append(signals)
        bundle.seo_pages.append(seo_signals)
        bundle.issues.extend(detect_aeo_geo_issues(signals))
        if len(bundle.pages) == 1:
            to_visit.extend(
                href
                for href in related_paths_for_aeo_geo(seo_signals, limit=max(0, max_pages - 1))
                if href not in seen
            )
    if bundle.pages:
        bundle.score = score_aeo_geo_issues(bundle.issues)
        bundle.crawled = True
    elif not bundle.issues:
        bundle.crawled = False
        bundle.score = 0
        bundle.crawl_error = bundle.crawl_error or "no pages scraped"
    return bundle


def _heuristic_review_markdown(
    *,
    title: str,
    brand: Brand,
    bundle: AeoGeoBundle,
    one_thing: str,
    owned: bool,
    prompt_seeds: list[str],
) -> str:
    today = datetime.now(UTC).date().isoformat()
    display = BRAND_DISPLAY.get(brand, brand.value)
    lines = [
        f"# {title}",
        (
            f"{today} · Score: {bundle.score}/100 · Basis: Firecrawl extractability signals "
            f"(no live citation vendor) · Brand: {display}"
        ),
        "",
        "## The one thing",
        one_thing,
        "",
        "## AEO scorecard (extractability)",
        (
            f"Heuristic score {bundle.score}/100 from {len(bundle.issues)} issue(s) "
            f"across {len(bundle.pages)} page(s). Not a live snippet or AI Overview rank."
        ),
        "",
        "## GEO scorecard (citability readiness)",
        "Citability requires quotable evidence and entity clarity — see issues below.",
        "",
        "## Access & crawlers",
        (
            "- Allow Googlebot and OAI-SearchBot for search appearance. "
            "GPTBot/ClaudeBot/Google-Extended are separate training decisions."
        ),
        "- Confirm important copy is in HTML text, not JS-only shells.",
        "- [NEED: robots.txt audit on live site] — this agent does not fetch robots.txt.",
        "",
        "## Entity kit",
    ]
    entity_issues = [i for i in bundle.issues if i.lever == "entity"]
    if entity_issues:
        for issue in entity_issues[:5]:
            lines.append(f"- **{issue.title}** — {issue.how_to_fix}")
    else:
        lines.append("- No entity gaps flagged from the scrape.")
    lines.extend(["", "## Quotable pages & fan-out gaps"])
    quotable = [i for i in bundle.issues if i.lever in {"quotable", "fanout"}]
    if quotable:
        for issue in quotable[:8]:
            lines.append(
                f"- **{issue.title}** ({issue.severity}) on `{issue.url}` — {issue.how_to_fix}"
            )
    else:
        lines.append("- No major quotable-structure gaps from the crawl.")
    lines.extend(["", "## Off-site corroboration [NEED]"])
    lines.append(
        "- [NEED: prompt panel / press mentions / review platforms] — "
        "this stack does not scrape Reddit or invent citation counts."
    )
    if brand == Brand.TACTIC_STUDIO:
        lines.append(
            "- tactic.studio outreach: research and document only — Pete + naming-rights gate."
        )
    lines.extend(["", "## Measurement panel (prompts × engines)"])
    if prompt_seeds:
        lines.append("Seed questions for human panel runs (mention / citation / accuracy):")
        for seed in prompt_seeds[:8]:
            lines.append(f"- `{seed}` → [NEED: ChatGPT, Gemini, Perplexity, Copilot]")
    else:
        lines.append("[NEED: brand prompt panel seeds]")
    lines.extend(
        [
            "",
            "## What I couldn't determine",
            "- Live citation or mention counts across chat engines.",
            "- GSC Search generative AI / Bing AI Performance exports.",
            "- robots.txt and WAF allowlists (human must verify).",
        ]
    )
    if bundle.crawl_error:
        lines.append(f"- Crawl error: {bundle.crawl_error}")
    if not owned:
        lines.append("- Competitor review — no implementation plan will be written.")
    lines.extend(
        [
            "",
            "## Method",
            (
                "Local Firecrawl scrape plus AEO/GEO extractability heuristics. "
                "SEO = blue-link rank; AEO = extractable answers; GEO = chat citations/mentions. "
                "This stack does not implement fixes or change live sites."
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
    prompt_seeds: list[str],
) -> str:
    lines = [
        f"# {title}",
        f"Derived from review: {review_title}"
        + (f" · Score {score}/100" if score is not None else ""),
        "",
        "## Do not implement from this agent",
        "This document is for humans. The Agency will not change the live site.",
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
    lines.extend(["", "## Fan-out pages to create (if any)"])
    if prompt_seeds:
        for seed in prompt_seeds[:6]:
            lines.append(
                f"- Consider a page that answers `{seed}` with answer-first copy "
                f"and quotable facts. [NEED: SERP/chat citation check]."
            )
    else:
        lines.append("- [NEED: prompt panel seeds]")
    lines.extend(
        [
            "",
            "## Measurement panel setup",
            (
                "Run each seed question in ChatGPT, Gemini, Perplexity, and Copilot. "
                "Record mention vs citation vs accuracy. Use GSC generative-AI reports and "
                "Bing Webmaster Tools AI Performance when available."
            ),
            "",
            "## Out of scope / [NEED]",
            "- Reddit spam, purchased citations, llms.txt ranking hacks.",
            "- tactic.studio outreach (Pete + naming-rights).",
            "- Invented citation counts or Search Console numbers.",
        ]
    )
    return "\n".join(lines)


def _tasks_from_issues(
    issues: list[AeoGeoIssue], *, prompt_seeds: list[str]
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for index, issue in enumerate(issues[:8], start=1):
        effort = "M" if issue.issue_id in {"thin-extractable-content", "no-quotable-evidence"} else "S"
        tasks.append(
            {
                "priority": index,
                "effort": effort,
                "page": issue.url,
                "task": issue.title,
                "implement": issue.how_to_fix,
                "verify": "Re-scrape or view source; confirm the evidence string is addressed.",
            }
        )
    if prompt_seeds and len(tasks) < 10:
        tasks.append(
            {
                "priority": len(tasks) + 1,
                "effort": "M",
                "page": issues[0].url if issues else "[NEED: URL]",
                "task": "Create fan-out page for a prompt-panel question",
                "implement": (
                    f"Add a page that answers `{prompt_seeds[0]}` with answer-first copy, "
                    "a table or FAQ, and at least one sourced statistic."
                ),
                "verify": "Human runs the prompt in ChatGPT and checks if the page is cited.",
            }
        )
    return tasks


def _llm_review(
    *,
    url: str,
    brand: Brand,
    owned: bool,
    bundle: AeoGeoBundle,
    prompt_seeds: list[str],
    fallback_title: str,
    fallback_body: str,
    fallback_one_thing: str,
) -> tuple[str, int, str, str] | None:
    settings = get_settings()
    brand_context = brand_context_for(ACTOR, brand, max_chars=700, channel="aeo_geo")
    review_guidance = (
        review_writer_guidance()
        if has_skill(ACTOR, "aeo-geo") or has_skill(ACTOR, "aeo-geo/aeo-geo-review")
        else "Write an AEO/GEO review document. Do not implement changes on any website."
    )
    system = (
        review_guidance
        + " You output JSON only."
        + UNTRUSTED_DATA_SYSTEM_SUFFIX
    )
    if brand_context:
        system += f"\n\n--- brand context (excerpt) ---\n{brand_context}"
    register = "owned site (write a fix-list)" if owned else "competitor (no implementation plan)"
    user = (
        f"Brand: {brand.value}\nRegister: {register}\nKind: geo (AEO/GEO combined)\n"
        f"{wrap_untrusted('url', url, max_chars=400)}\n"
        f"{wrap_untrusted('issues', json.dumps([i.as_dict() for i in bundle.issues])[:4000], max_chars=4000)}\n"
        f"{wrap_untrusted('signals', json.dumps(bundle.as_evidence())[:3500], max_chars=3500)}\n"
        f"{wrap_untrusted('prompt_seeds', json.dumps(prompt_seeds)[:1500], max_chars=1500)}\n"
        f"{wrap_untrusted('heuristic_draft', fallback_body, max_chars=2500)}\n\n"
        'Return JSON: {"title": "...", "score": 0, "one_thing": "...", "body": "markdown"}. '
        "Score must stay a heuristic 0-100."
    )
    try:
        record_heartbeat(
            ACTOR,
            status=AgentStatus.WORKING,
            task=f"writing AEO/GEO review for {url}",
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
            task=f"aeo/geo review {url[:40]}",
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
    bundle: AeoGeoBundle,
    review_title: str,
    review_body: str,
    one_thing: str,
    fallback_title: str,
    fallback_body: str,
    fallback_tasks: list[dict[str, Any]],
) -> tuple[str, str, str, list[dict[str, Any]]] | None:
    settings = get_settings()
    brand_context = brand_context_for(ACTOR, brand, max_chars=600, channel="aeo_geo")
    plan_guidance = (
        plan_writer_guidance()
        if has_skill(ACTOR, "aeo-geo") or has_skill(ACTOR, "aeo-geo/aeo-geo-plan")
        else "Write an AEO/GEO implementation plan for a human to apply on the target site."
    )
    system = (
        plan_guidance
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
        'Return JSON: {"title": "...", "one_thing": "...", "body": "markdown", '
        '"tasks": [{"priority": 1, "effort": "S", "page": "...", '
        '"task": "...", "implement": "...", "verify": "..."}]}'
    )
    try:
        record_heartbeat(
            ACTOR,
            status=AgentStatus.WORKING,
            task=f"writing AEO/GEO plan for {url}",
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
            task=f"aeo/geo plan {url[:40]}",
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
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None
