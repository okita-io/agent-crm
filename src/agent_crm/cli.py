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
from .enums import Brand


def _cmd_init_db(_args: argparse.Namespace) -> int:
    from .db import init_db

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


def _parse_brand(value: str | None) -> Brand:
    if not value:
        return Brand.UNASSIGNED
    return Brand(value)


def _cmd_hunt(args: argparse.Namespace) -> int:
    from .db import init_db
    from .outbound_hunter import OutboundHunter

    init_db()
    hunter = OutboundHunter()
    result = hunter.hunt_once(
        args.query,
        brand=_parse_brand(args.brand),
        max_pages=args.max_pages,
    )
    print(json.dumps(result, indent=2))
    return 0


def _cmd_hunt_loop(args: argparse.Namespace) -> int:
    from .db import init_db
    from .outbound_hunter import HuntBudget, OutboundHunter

    init_db()
    hunter = OutboundHunter()
    budget = HuntBudget(
        max_queries=args.max_queries,
        max_minutes=args.max_minutes,
        max_pages_per_query=args.max_pages_per_query,
    )
    result = hunter.hunt_loop(
        query=args.query,
        brand=_parse_brand(args.brand),
        budget=budget,
        resume=not args.no_resume,
    )
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "queries_run": result.queries_run,
                "resources_found": result.resources_found,
                "leads_created": result.leads_created,
                "branch_terms_enqueued": result.branch_terms_enqueued,
                "stop_reason": result.stop_reason,
            },
            indent=2,
        )
    )
    return 0


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

    hunt = sub.add_parser("hunt", help="One-shot outbound hunt")
    hunt.add_argument("query", help="Search query")
    hunt.add_argument("--brand", help="Brand slug (midnightsatin, celestial-nexus, heybuddy)")
    hunt.add_argument("--max-pages", type=int, default=None, help="Pages to scrape")
    hunt.set_defaults(func=_cmd_hunt)

    hunt_loop = sub.add_parser("hunt-loop", help="Bounded branching hunt loop")
    hunt_loop.add_argument("query", nargs="?", default=None, help="Seed query (optional)")
    hunt_loop.add_argument("--brand", help="Brand slug; uses seed pack when no query")
    hunt_loop.add_argument("--max-queries", type=int, default=20)
    hunt_loop.add_argument("--max-minutes", type=int, default=25)
    hunt_loop.add_argument("--max-pages-per-query", type=int, default=8)
    hunt_loop.add_argument(
        "--no-resume",
        action="store_true",
        help="Do not resume pending queries from prior runs",
    )
    hunt_loop.set_defaults(func=_cmd_hunt_loop)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
