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
        summarize=not args.no_summarize,
        write_accounts=not args.no_accounts,
    )
    result = run_research(request)
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    return 0 if result.findings_written or not result.errors else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-crm", description="Agent CRM tools")
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
        choices=["midnightsatin", "celestial-nexus", "heybuddy"],
        help="Optional brand to route discovered leads",
    )
    hunt.add_argument("--max-pages", type=int, default=8, help="Max pages to scrape (1-10)")
    hunt.add_argument(
        "--search-limit",
        type=int,
        default=15,
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

    hunt_loop = sub.add_parser("hunt-loop", help="Bounded branching hunt loop")
    hunt_loop.add_argument("query", nargs="?", default=None, help="Seed query (optional)")
    hunt_loop.add_argument(
        "--brand",
        choices=["midnightsatin", "celestial-nexus", "heybuddy"],
        help="Brand slug; uses seed pack when no query",
    )
    hunt_loop.add_argument("--max-queries", type=int, default=20)
    hunt_loop.add_argument("--max-minutes", type=int, default=25)
    hunt_loop.add_argument("--max-pages-per-query", type=int, default=8)
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
        help="Run a Research agent cycle (competitor or nonprofit prospecting)",
    )
    research.add_argument(
        "--brand",
        required=True,
        choices=["midnightsatin", "celestial-nexus", "heybuddy"],
        help="Brand context for the research run",
    )
    research.add_argument(
        "--kind",
        choices=["competitor", "nonprofit", "other"],
        help="Finding kind (defaults from brand: heybuddy->nonprofit, others->competitor)",
    )
    research.add_argument(
        "query",
        nargs="?",
        help="Optional single seed query (otherwise uses brand seed pack)",
    )
    research.add_argument("--max-queries", type=int, default=12, help="Max search queries")
    research.add_argument("--max-pages", type=int, default=4, help="Max pages to scrape")
    research.add_argument("--max-minutes", type=int, default=20, help="Wall-clock budget")
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
