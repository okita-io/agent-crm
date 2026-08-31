"""Small CLI entry point: ``agent-crm <command>``.

Commands:
    init-db   Create tables directly (dev/demo; Alembic is the real path).
    serve     Run the FastAPI service via uvicorn.
    seed      Insert a couple of demo leads so the dashboard is not empty.
    report    Print the weekly report as JSON.
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import get_settings


def _cmd_init_db(_args: argparse.Namespace) -> int:
    from .db import init_db

    settings = get_settings()
    if not settings.is_sqlite:
        print("Postgres schema is managed by Alembic (alembic upgrade head).")
        return 0
    init_db()
    print("Tables created.")
    return 0


def _cmd_serve(_args: argparse.Namespace) -> int:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "agent_crm.api:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )
    return 0


def _cmd_seed(_args: argparse.Namespace) -> int:
    from .db import init_db
    from .enums import Brand, LeadSource, Priority, Stage
    from .pipeline import PipelineManager
    from .schemas import EnrichmentInput, LeadCreate, ScoreInput
    from .tooling import CRMToolkit

    init_db()
    intake = CRMToolkit(actor="lead_intake")
    scorer = CRMToolkit(actor="lead_scoring")
    router = CRMToolkit(actor="brand_router")
    research = CRMToolkit(actor="research")
    pm = PipelineManager()

    lead = intake.create_lead(
        LeadCreate(
            name="Ada Vega",
            email="ada@nova-studio.example",
            company="Nova Studio",
            source=LeadSource.FORM,
            raw_payload={"message": "Need a bold brand refresh for a launch."},
        )
    )
    scorer.record_score(lead.id, ScoreInput(score=88, priority=Priority.HIGH))
    router.route_brand(lead.id, Brand.MIDNIGHTSATIN)
    pm.evaluate_hot(lead.id)
    pm.transition(lead.id, Stage.SCORED)
    research.record_enrichment(
        lead.id,
        EnrichmentInput(
            summary="Boutique design studio, 8 people, launching a product line in Q4.",
            website="https://nova-studio.example",
            socials={"instagram": "@novastudio"},
        ),
    )
    pm.transition(lead.id, Stage.ENRICHED)

    lead2 = intake.create_lead(
        LeadCreate(
            name="Sam Okafor",
            email="sam@heybuddy-fans.example",
            source=LeadSource.DM,
            raw_payload={"message": "loved the app, can we collab?"},
        )
    )
    scorer.record_score(lead2.id, ScoreInput(score=54, priority=Priority.MEDIUM))
    router.route_brand(lead2.id, Brand.HEYBUDDY)

    print(f"Seeded leads {lead.id} and {lead2.id}.")
    return 0


def _cmd_report(_args: argparse.Namespace) -> int:
    from .pipeline import PipelineManager

    print(json.dumps(PipelineManager().weekly_report(), indent=2))
    return 0


def _cmd_hunt(args: argparse.Namespace) -> int:
    from .db import init_db
    from .enums import Brand
    from .outbound_hunter import run_hunt
    from .schemas import HuntRequest

    init_db()
    brand = Brand(args.brand) if args.brand else None
    request = HuntRequest(
        query=args.query,
        brand=brand,
        max_pages=args.max_pages,
        search_limit=args.search_limit,
        transition_to_prospect=not args.no_prospect,
        summarize=not args.no_summarize,
    )
    result = run_hunt(request)
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    return 0 if not result.errors or result.leads_created else 1


def _cmd_hunt_loop(args: argparse.Namespace) -> int:
    from .db import init_db
    from .enums import Brand
    from .hunt_loop import HuntBudget, run_hunt_loop

    init_db()
    brand = Brand(args.brand) if args.brand else Brand.UNASSIGNED
    budget = HuntBudget(
        max_queries=args.max_queries,
        max_minutes=args.max_minutes,
        max_pages_per_query=args.max_pages_per_query,
    )
    result = run_hunt_loop(
        query=args.query,
        brand=brand,
        budget=budget,
        resume=not args.no_resume,
        summarize_branches=not args.no_summarize,
    )
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "queries_run": result.queries_run,
                "resources_found": result.resources_found,
                "branch_terms_enqueued": result.branch_terms_enqueued,
                "community_terms_enqueued": result.community_terms_enqueued,
                "person_terms_enqueued": result.person_terms_enqueued,
                "engagement_terms_enqueued": result.engagement_terms_enqueued,
                "stop_reason": result.stop_reason,
            },
            indent=2,
        )
    )
    return 0


def _cmd_research(args: argparse.Namespace) -> int:
    from .db import init_db
    from .enums import Brand, ResearchFindingKind
    from .research import run_research
    from .schemas import ResearchRequest

    init_db()
    brand = Brand(args.brand)
    kind = ResearchFindingKind(args.kind) if args.kind else None
    request = ResearchRequest(
        brand=brand,
        kind=kind,
        query=args.query,
        max_queries=args.max_queries,
        max_pages=args.max_pages,
        max_minutes=args.max_minutes,
        search_limit=args.search_limit,
        summarize=not args.no_summarize,
        write_accounts=not args.no_accounts,
    )
    result = run_research(request)
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    return 0 if result.findings_written or not result.errors else 1


def _cmd_research_loop(args: argparse.Namespace) -> int:
    from .db import init_db
    from .research_loop import ResearchLoopBudget, run_research_loop

    init_db()
    budget = ResearchLoopBudget(
        max_queries=args.max_queries,
        max_pages=args.max_pages,
        max_minutes=args.max_minutes,
        search_limit=args.search_limit,
    )
    result = run_research_loop(
        budget=budget,
        summarize=not args.no_summarize,
        write_accounts=not args.no_accounts,
    )
    print(
        json.dumps(
            {
                "queries_run": result.queries_run,
                "pages_scraped": result.pages_scraped,
                "findings_written": result.findings_written,
                "follow_up_terms_enqueued": result.follow_up_terms_enqueued,
                "stop_reason": result.stop_reason,
                "errors": result.errors,
            },
            indent=2,
        )
    )
    return 0 if result.findings_written or not result.errors else 1


def _cmd_engagement_loop(args: argparse.Namespace) -> int:
    from .db import init_db
    from .engagement_loop import EngagementBudget, run_engagement_loop
    from .enums import Brand

    init_db()
    brand = Brand(args.brand) if args.brand else None
    budget = EngagementBudget(
        max_venues=args.max_venues,
        max_pages_per_venue=args.max_pages_per_venue,
        max_minutes=args.max_minutes,
    )
    result = run_engagement_loop(
        brand=brand,
        budget=budget,
        summarize=not args.no_summarize,
    )
    print(
        json.dumps(
            {
                "venues_scanned": result.venues_scanned,
                "threads_cataloged": result.threads_cataloged,
                "drafts_written": result.drafts_written,
                "pages_scraped": result.pages_scraped,
                "follow_up_terms_enqueued": result.follow_up_terms_enqueued,
                "stop_reason": result.stop_reason,
                "errors": result.errors,
            },
            indent=2,
        )
    )
    return 0 if result.venues_scanned or result.threads_cataloged or not result.errors else 1


def _cmd_aeo_geo_loop(args: argparse.Namespace) -> int:
    from .aeo_geo_loop import run_aeo_geo_loop, run_aeo_geo_loop_watch
    from .db import init_db
    from .enums import Brand
    from .seo_loop import SeoBudget

    init_db()
    brand = Brand(args.brand) if args.brand else None
    budget = SeoBudget(
        max_targets=args.max_targets,
        max_pages_per_target=args.max_pages_per_target,
        max_minutes=args.max_minutes,
    )
    if args.watch:
        run_aeo_geo_loop_watch(
            brand=brand,
            budget=budget,
            summarize=not args.no_summarize,
        )
        return 0
    result = run_aeo_geo_loop(
        brand=brand,
        budget=budget,
        summarize=not args.no_summarize,
    )
    print(
        json.dumps(
            {
                "targets_processed": result.targets_processed,
                "reviews_written": result.reviews_written,
                "plans_written": result.plans_written,
                "pages_scraped": result.pages_scraped,
                "stop_reason": result.stop_reason,
                "errors": result.errors,
            },
            indent=2,
        )
    )
    return 0 if result.reviews_written or result.plans_written or not result.errors else 1


def _cmd_seo_loop(args: argparse.Namespace) -> int:
    from .db import init_db
    from .enums import Brand
    from .seo_loop import SeoBudget, run_seo_loop, run_seo_loop_watch

    init_db()
    brand = Brand(args.brand) if args.brand else None
    budget = SeoBudget(
        max_targets=args.max_targets,
        max_pages_per_target=args.max_pages_per_target,
        max_minutes=args.max_minutes,
    )
    if args.watch:
        run_seo_loop_watch(
            brand=brand,
            budget=budget,
            summarize=not args.no_summarize,
        )
        return 0
    result = run_seo_loop(
        brand=brand,
        budget=budget,
        summarize=not args.no_summarize,
    )
    print(
        json.dumps(
            {
                "targets_processed": result.targets_processed,
                "reviews_written": result.reviews_written,
                "plans_written": result.plans_written,
                "pages_scraped": result.pages_scraped,
                "stop_reason": result.stop_reason,
                "errors": result.errors,
            },
            indent=2,
        )
    )
    return 0 if result.reviews_written or result.plans_written or not result.errors else 1


def _cmd_contacts(args: argparse.Namespace) -> int:
    from .contact_store import (
        backfill_contact_enrichment,
        backfill_contact_quality,
        list_contact_profiles,
    )
    from .db import init_db
    from .enums import Brand, ContactAudience

    init_db()
    command = getattr(args, "contacts_command", None)
    if command == "backfill":
        result = backfill_contact_quality(limit=args.limit, dry_run=args.dry_run)
        print(json.dumps(result.model_dump(mode="json"), indent=2))
        return 0 if not result.errors else 1

    if command == "enrich":
        result = backfill_contact_enrichment(limit=args.limit, dry_run=args.dry_run)
        print(json.dumps(result.model_dump(mode="json"), indent=2))
        return 0 if not result.errors else 1

    brand = Brand(args.brand) if args.brand else None
    audience = ContactAudience(args.audience) if args.audience else None
    profiles = list_contact_profiles(brand=brand, audience=audience, email=args.email, limit=args.limit)
    rows = [profile.model_dump(mode="json") for profile in profiles]
    print(json.dumps(rows, indent=2))
    return 0


def _cmd_jobs(args: argparse.Namespace) -> int:
    from .db import init_db
    from .job_dispatcher import run_job_dispatcher

    init_db()
    run_job_dispatcher(
        batch_size=args.batch_size,
        poll_seconds=args.poll_seconds,
    )
    return 0


def _cmd_orchestrate(args: argparse.Namespace) -> int:
    from .db import init_db
    from .orchestrator import run_orchestrator

    init_db()
    run_orchestrator(poll_seconds=args.poll_seconds)
    return 0


def _cmd_queue_review(args: argparse.Namespace) -> int:
    from .config import get_settings
    from .db import init_db
    from .queue_review import QueueReviewBudget, run_queue_review, run_queue_review_watch

    init_db()
    settings = get_settings()
    budget = QueueReviewBudget(
        max_queries=args.max_queries
        if args.max_queries is not None
        else settings.queue_review_max_queries,
        max_minutes=args.max_minutes,
        allow_spark=not args.no_spark,
        spark_per_cycle=settings.queue_review_spark_per_cycle,
    )
    if args.watch:
        run_queue_review_watch(budget=budget)
        return 0
    result = run_queue_review(budget=budget)
    print(
        json.dumps(
            {
                "reviewed": result.reviewed,
                "kept": result.kept,
                "tossed": result.tossed,
                "spark_used": result.spark_used,
                "stop_reason": result.stop_reason,
                "errors": result.errors,
            },
            indent=2,
        )
    )
    return 0


def _cmd_purge_noise(args: argparse.Namespace) -> int:
    from .db import init_db
    from .noise_purge import purge_denied_ingest

    init_db()
    result = purge_denied_ingest(dry_run=not args.apply)
    print(json.dumps(result, indent=2))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    from .db import init_db
    from .schemas import VerifyRawRequest
    from .verifier import verify_batch_unverified, verify_lead, verify_raw

    init_db()

    if args.email or args.url:
        result = verify_raw(VerifyRawRequest(email=args.email, url=args.url))
        print(json.dumps(result.model_dump(mode="json"), indent=2))
        return 0

    if args.lead_id is not None:
        results = verify_lead(args.lead_id)
        print(json.dumps([r.model_dump(mode="json") for r in results], indent=2))
        return 0

    if args.unverified:
        result = verify_batch_unverified(limit=args.limit)
        print(json.dumps(result.model_dump(mode="json"), indent=2))
        return 0 if not result.errors else 1

    print("Specify --lead-id, --unverified, --email, or --url.", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-crm",
        description="The Agency — CRM, SEO, and AEO/GEO document tools (repo: agent-crm)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create tables directly").set_defaults(
        func=_cmd_init_db
    )
    sub.add_parser("serve", help="Run the FastAPI service").set_defaults(
        func=_cmd_serve
    )
    sub.add_parser("seed", help="Insert demo leads").set_defaults(func=_cmd_seed)
    sub.add_parser("report", help="Print the weekly report").set_defaults(
        func=_cmd_report
    )

    hunt = sub.add_parser("hunt", help="Run one Outbound Hunter search cycle")
    hunt.add_argument("query", help="Search query (e.g. boutique design studio NYC)")
    hunt.add_argument(
        "--brand",
        choices=["midnightsatin", "celestial-nexus", "heybuddy", "tactic-studio"],
        help="Optional brand to route discovered leads",
    )
    hunt.add_argument("--max-pages", type=int, default=50, help="Max pages to scrape")
    hunt.add_argument(
        "--search-limit",
        type=int,
        default=50,
        help="Max SearXNG results to consider",
    )
    hunt.add_argument(
        "--no-prospect",
        action="store_true",
        help="Do not move new leads to the prospect stage",
    )
    hunt.add_argument(
        "--no-summarize",
        action="store_true",
        help="Skip Spark LLM summarization",
    )
    hunt.set_defaults(func=_cmd_hunt)

    hunt_loop = sub.add_parser(
        "hunt-loop",
        help="Bounded branching hunt loop (no --brand drains all brands by priority)",
    )
    hunt_loop.add_argument("query", nargs="?", default=None, help="Seed query (optional)")
    hunt_loop.add_argument(
        "--brand",
        choices=["midnightsatin", "celestial-nexus", "heybuddy", "tactic-studio"],
        help="Brand slug; omit for global priority queue across all brands",
    )
    hunt_loop.add_argument(
        "--max-queries",
        type=int,
        default=0,
        help="Query budget (0 = unlimited)",
    )
    hunt_loop.add_argument(
        "--max-minutes",
        type=int,
        default=0,
        help="Wall-clock budget in minutes (0 = unlimited)",
    )
    hunt_loop.add_argument("--max-pages-per-query", type=int, default=50)
    hunt_loop.add_argument(
        "--no-resume",
        action="store_true",
        help="Do not resume pending queries from prior runs",
    )
    hunt_loop.add_argument(
        "--no-summarize",
        action="store_true",
        help="Skip Spark LLM branch-term extraction",
    )
    hunt_loop.set_defaults(func=_cmd_hunt_loop)

    research = sub.add_parser(
        "research",
        help="Run a Research agent cycle (competitor, nonprofit, ad-placement, or target-company prospecting)",
    )
    research.add_argument(
        "--brand",
        required=True,
        choices=["midnightsatin", "celestial-nexus", "heybuddy", "tactic-studio"],
        help="Brand context for the research run",
    )
    research.add_argument(
        "--kind",
        choices=["competitor", "nonprofit", "ad_placement", "target_company", "other"],
        help="Finding kind (defaults from brand: heybuddy->nonprofit, tactic-studio->target_company)",
    )
    research.add_argument(
        "query",
        nargs="?",
        help="Optional single seed query (otherwise uses brand seed pack)",
    )
    research.add_argument("--max-queries", type=int, default=20, help="Max search queries")
    research.add_argument("--max-pages", type=int, default=200, help="Max pages to scrape")
    research.add_argument("--max-minutes", type=int, default=60, help="Wall-clock budget")
    research.add_argument(
        "--search-limit",
        type=int,
        default=50,
        help="Max SearXNG results per query",
    )
    research.add_argument(
        "--no-summarize",
        action="store_true",
        help="Skip Spark LLM summarization",
    )
    research.add_argument(
        "--no-accounts",
        action="store_true",
        help="Do not write Account notes for strong hits",
    )
    research.set_defaults(func=_cmd_research)

    research_loop = sub.add_parser(
        "research-loop",
        help="Drain the append-only research queue across all four brands (0 budgets = unlimited)",
    )
    research_loop.add_argument(
        "--max-queries",
        type=int,
        default=0,
        help="Query budget (0 = unlimited)",
    )
    research_loop.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Page scrape budget (0 = unlimited)",
    )
    research_loop.add_argument(
        "--max-minutes",
        type=int,
        default=0,
        help="Wall-clock budget in minutes (0 = unlimited)",
    )
    research_loop.add_argument(
        "--search-limit",
        type=int,
        default=50,
        help="Max SearXNG results per query",
    )
    research_loop.add_argument(
        "--no-summarize",
        action="store_true",
        help="Skip Spark LLM summarization",
    )
    research_loop.add_argument(
        "--no-accounts",
        action="store_true",
        help="Do not write Account notes for strong hits",
    )
    research_loop.set_defaults(func=_cmd_research_loop)

    engagement_loop = sub.add_parser(
        "engagement-loop",
        help="Drain the append-only engagement queue and draft comment replies (never posts)",
    )
    engagement_loop.add_argument(
        "--brand",
        choices=["midnightsatin", "celestial-nexus", "heybuddy", "tactic-studio"],
        help="Brand slug; omit to scan all brands",
    )
    engagement_loop.add_argument(
        "--max-venues",
        type=int,
        default=10,
        help="Max queued searches to run this cycle",
    )
    engagement_loop.add_argument(
        "--max-pages-per-venue",
        type=int,
        default=15,
        help="Max pages scraped per venue",
    )
    engagement_loop.add_argument(
        "--max-minutes",
        type=int,
        default=45,
        help="Wall-clock budget in minutes (0 = unlimited)",
    )
    engagement_loop.add_argument(
        "--no-summarize",
        action="store_true",
        help="Skip Spark LLM comment drafts",
    )
    engagement_loop.set_defaults(func=_cmd_engagement_loop)

    seo_loop = sub.add_parser(
        "seo-loop",
        help="Write SEO review and plan documents for brand sites (never implements)",
    )
    seo_loop.add_argument(
        "--brand",
        choices=["midnightsatin", "celestial-nexus", "heybuddy", "tactic-studio"],
        help="Brand slug; omit to process all brands",
    )
    seo_loop.add_argument(
        "--max-targets",
        type=int,
        default=8,
        help="Max sites to write documents for this cycle (0 = unlimited)",
    )
    seo_loop.add_argument(
        "--max-pages-per-target",
        type=int,
        default=4,
        help="Max pages scraped per site",
    )
    seo_loop.add_argument(
        "--max-minutes",
        type=int,
        default=45,
        help="Wall-clock budget in minutes (0 = unlimited)",
    )
    seo_loop.add_argument(
        "--watch",
        action="store_true",
        help="Stay running: drain due targets, then wait until the next local noon",
    )
    seo_loop.add_argument(
        "--no-summarize",
        action="store_true",
        help="Skip Spark LLM document writing (heuristic markdown only)",
    )
    seo_loop.set_defaults(func=_cmd_seo_loop)

    aeo_geo_loop = sub.add_parser(
        "aeo-geo-loop",
        help="Write AEO/GEO review and plan documents for brand sites (never implements)",
    )
    aeo_geo_loop.add_argument(
        "--brand",
        choices=["midnightsatin", "celestial-nexus", "heybuddy", "tactic-studio"],
        help="Brand slug; omit to process all brands",
    )
    aeo_geo_loop.add_argument(
        "--max-targets",
        type=int,
        default=8,
        help="Max sites to write documents for this cycle (0 = unlimited)",
    )
    aeo_geo_loop.add_argument(
        "--max-pages-per-target",
        type=int,
        default=4,
        help="Max pages scraped per site",
    )
    aeo_geo_loop.add_argument(
        "--max-minutes",
        type=int,
        default=45,
        help="Wall-clock budget in minutes (0 = unlimited)",
    )
    aeo_geo_loop.add_argument(
        "--watch",
        action="store_true",
        help="Stay running: drain due targets, then wait until the next local noon",
    )
    aeo_geo_loop.add_argument(
        "--no-summarize",
        action="store_true",
        help="Skip Spark LLM document writing (heuristic markdown only)",
    )
    aeo_geo_loop.set_defaults(func=_cmd_aeo_geo_loop)

    contacts = sub.add_parser("contacts", help="List contact profiles extracted from scrapes")
    contacts_sub = contacts.add_subparsers(dest="contacts_command", required=True)
    contacts_list = contacts_sub.add_parser("list", help="List contact profiles")
    contacts_list.add_argument(
        "--brand",
        choices=["midnightsatin", "celestial-nexus", "heybuddy", "tactic-studio", "unassigned"],
        help="Filter by brand",
    )
    contacts_list.add_argument(
        "--audience",
        choices=["marketing", "influencer", "user", "end_user", "b2b", "client"],
        help="Filter by audience bucket",
    )
    contacts_list.add_argument("--email", help="Filter by exact email")
    contacts_list.add_argument("--limit", type=int, default=500, help="Max profiles to return")
    contacts_list.set_defaults(func=_cmd_contacts)

    contacts_backfill = contacts_sub.add_parser(
        "backfill",
        help="Re-apply contact-quality filters to existing profiles",
    )
    contacts_backfill.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Max profiles to scan (default 500)",
    )
    contacts_backfill.add_argument(
        "--dry-run",
        action="store_true",
        help="Report changes without writing",
    )
    contacts_backfill.set_defaults(func=_cmd_contacts)

    contacts_enrich = contacts_sub.add_parser(
        "enrich",
        help="Backfill public people-enrichment for existing profiles",
    )
    contacts_enrich.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Max profiles to scan (default 500)",
    )
    contacts_enrich.add_argument(
        "--dry-run",
        action="store_true",
        help="Report enrichment without writing",
    )
    contacts_enrich.set_defaults(func=_cmd_contacts)

    jobs = sub.add_parser(
        "jobs",
        help="Run the agent job dispatcher (enrich, verify; Spark-aware)",
    )
    jobs.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Max jobs to claim per cycle (default from settings)",
    )
    jobs.add_argument(
        "--poll-seconds",
        type=int,
        default=None,
        help="Idle poll interval when queues are empty (default from settings)",
    )
    jobs.set_defaults(func=_cmd_jobs)

    orchestrate = sub.add_parser(
        "orchestrate",
        help="Run orchestrator loop (stack health + improvement notes)",
    )
    orchestrate.add_argument(
        "--poll-seconds",
        type=int,
        default=None,
        help="Inspection interval in seconds (default from settings)",
    )
    orchestrate.set_defaults(func=_cmd_orchestrate)

    queue_review = sub.add_parser(
        "queue-review",
        help="Keep or toss hunter-added search-queue terms before they run",
    )
    queue_review.add_argument(
        "--watch",
        action="store_true",
        help="Keep reviewing as the hunter enqueues new terms",
    )
    queue_review.add_argument(
        "--max-queries",
        type=int,
        default=None,
        help="Review budget per cycle (default from settings)",
    )
    queue_review.add_argument(
        "--max-minutes",
        type=int,
        default=10,
        help="Wall-clock budget in minutes (0 = unlimited)",
    )
    queue_review.add_argument(
        "--no-spark",
        action="store_true",
        help="Deterministic review only (no Spark)",
    )
    queue_review.set_defaults(func=_cmd_queue_review)

    purge_noise = sub.add_parser(
        "purge-noise",
        help="Drop deny-listed hunt resources and mark sourced contacts junk",
    )
    purge_noise.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default is dry-run)",
    )
    purge_noise.set_defaults(func=_cmd_purge_noise)

    verify = sub.add_parser("verify", help="Verify lead contacts (DNS/MX/HTTP, no mail)")
    verify.add_argument("--lead-id", type=int, help="Verify a single lead by id")
    verify.add_argument(
        "--unverified",
        action="store_true",
        help="Batch-verify unverified hunter leads",
    )
    verify.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max leads for --unverified (default 50)",
    )
    verify.add_argument("--email", help="Verify a raw email address")
    verify.add_argument("--url", help="Verify a raw URL")
    verify.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
