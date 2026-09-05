"""The FastAPI service.

Milestone 1 surface:
- ``GET  /health``            -- liveness + which store is attached
- ``POST /intake/webhook``    -- the Inbound Listener write path (form/DM/email -> row)
- ``GET  /leads``             -- list leads (dashboard / debugging)
- ``GET  /leads/{id}``        -- one lead
- ``GET  /leads/{id}/activities`` -- append-only history
- ``POST /leads/{id}/stage``  -- Pipeline Manager stage transition
- ``GET  /report/weekly``     -- Analytics weekly snapshot
- ``GET  /report/growth``     -- Catalog deltas for 1h / 4h / 24h

The API is a thin shell over the tooling + Pipeline Manager. It does not embed
business logic so the same operations work from an agent process without HTTP.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent_crm.aeo_geo.loop import run_aeo_geo_loop
from agent_crm.agency.request_store import create_agency_request, list_agency_requests
from agent_crm.contacts.comment_people_store import count_comment_people, list_comment_people
from agent_crm.contacts.growth import catalog_growth
from agent_crm.contacts.store import (
    backfill_contact_enrichment,
    backfill_contact_quality,
    count_contact_profiles,
    count_contact_profiles_by_brand,
    list_contact_profiles,
)
from agent_crm.contacts.verifier import (
    list_verifications,
    verify_batch_unverified,
    verify_lead,
    verify_raw,
)
from agent_crm.engagement.loop import EngagementBudget, run_engagement_loop
from agent_crm.engagement.store import list_drafts, list_threads
from agent_crm.hunt.loop import HuntBudget, run_hunt_loop
from agent_crm.hunt.outbound import run_hunt
from agent_crm.hunt.status import build_hunt_status
from agent_crm.hunt.store import HuntStore
from agent_crm.research.runner import run_research
from agent_crm.research.store import list_findings
from agent_crm.seo.loop import SeoBudget, run_seo_loop
from agent_crm.seo.store import list_plans, list_reviews, list_targets

from . import __version__
from .agent_control import set_agent_enabled
from .agent_query import (
    agent_catalog,
    agent_search,
    get_contact,
    get_finding,
    get_website,
    query_comment_people,
    query_contacts,
    query_engagement_threads,
    query_findings,
    query_pipeline_leads,
    query_seo_plans,
    query_seo_reviews,
    query_websites,
)
from .auth import require_api_token, require_known_agent
from .config import get_settings
from .db import database_kind, init_db
from .enums import (
    Brand,
    ContactAudience,
    HuntResourceKind,
    ImprovementNoteStatus,
    ResearchFindingKind,
    SeoPlanKind,
    SeoPlanStatus,
    SeoReviewKind,
    SeoReviewStatus,
    SeoTargetRole,
    Stage,
)
from .errors import ConflictError, NotFoundError, ValidationAppError
from .floor import build_queue_lanes
from .heartbeat import list_heartbeats, record_heartbeat
from .improvement_store import list_improvement_notes
from .pipeline import PipelineManager
from .presence import build_observer_rows, fetch_spark_queue_health, spark_slot_summary
from .runtime_settings_store import (
    list_runtime_settings_meta,
    probe_spark_upstream,
    update_runtime_settings,
)
from .skill_store import (
    assign_skill,
    catalog_with_usage,
    list_agent_skills,
    list_assignments_by_agent,
    unassign_skill,
    unassign_skill_everywhere,
)
from .schemas import (
    ActivityOut,
    AgencyRequestIn,
    AgencyRequestOut,
    AgencySettingsUpdateIn,
    AgentCatalogOut,
    AgentEnabledIn,
    AgentObserverOut,
    AgentPageOut,
    AgentSearchOut,
    AgentSkillIn,
    AgentSkillsOut,
    BatchVerifyRequest,
    BatchVerifyResult,
    CatalogGrowthOut,
    CommentPersonOut,
    ContactBackfillRequest,
    ContactBackfillResultOut,
    ContactEnrichRequest,
    ContactEnrichResultOut,
    ContactProfileOut,
    ContactProfilesSummaryOut,
    ContactVerificationOut,
    EngagementDraftOut,
    EngagementLoopRequest,
    EngagementLoopResultOut,
    EngagementThreadOut,
    HeartbeatIn,
    HeartbeatOut,
    HuntLoopRequest,
    HuntLoopResultOut,
    HuntQueueStatusOut,
    HuntRequest,
    HuntResourceOut,
    HuntResult,
    HuntStatusOut,
    ImprovementNoteOut,
    LeadCreate,
    LeadOut,
    OpportunityOut,
    PublishJobOut,
    PublishLoopRequest,
    PublishLoopResultOut,
    PublishScheduleRequest,
    ContentPackageScheduleRequest,
    ProjectChannelsIn,
    ProjectCreateIn,
    ProjectOut,
    ProjectPatchIn,
    ProjectPromptsIn,
    ProjectsListOut,
    ProjectStatsOut,
    QueueLaneOut,
    QueuesOut,
    ResearchFindingOut,
    ResearchRequest,
    ResearchResult,
    RuntimeSettingMetaOut,
    SeoLoopRequest,
    SocialAccountCreate,
    SocialAccountOut,
    SeoLoopResultOut,
    SeoPlanOut,
    SeoReviewOut,
    SeoTargetOut,
    SkillCatalogItemOut,
    SkillsOut,
    SparkProbeOut,
    SparkSlotSummaryOut,
    TregAllowIn,
    TregAllowResultOut,
    TregStatusOut,
    TregSyncResultOut,
    TregToolOut,
    VerifyRawRequest,
    VerifyRawResult,
)
from .tooling import CRMToolkit

app = FastAPI(
    title="The Agency",
    version=__version__,
    description=(
        "The Agency — local, agent-driven CRM plus SEO and AEO/GEO document workflows "
        "(never applied to live sites). Repository: okita-io/agent-crm."
    ),
    dependencies=[Depends(require_api_token)],
)


def _cors_origins() -> list[str]:
    raw = get_settings().cors_origins.strip()
    return [part.strip() for part in raw.split(",") if part.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    # SQLite: create tables on boot. Postgres: schema from Alembic (entrypoint migrate).
    init_db()


# ---- request/response models ----------------------------------------------


class HealthOut(BaseModel):
    status: str
    version: str
    database: str


class StageChangeIn(BaseModel):
    to_stage: Stage
    note: str | None = None


# ---- health ----------------------------------------------------------------


@app.get(
    "/health",
    response_model=HealthOut,
    tags=["system"],
    dependencies=[],
)
def health() -> HealthOut:
    return HealthOut(status="ok", version=__version__, database=database_kind())


# ---- intake (Inbound Listener) ---------------------------------------------


@app.post("/intake/webhook", response_model=LeadOut, status_code=201, tags=["intake"])
def intake_webhook(payload: LeadCreate) -> LeadOut:
    """Create a lead from an inbound submission.

    Inbound is a write path, not a conversation: create the record and hand off.
    """
    crm = CRMToolkit(actor="lead_intake")
    return crm.create_lead(payload)


# ---- outbound hunter -------------------------------------------------------


@app.post("/hunt", response_model=HuntResult, tags=["hunter"])
def hunt(payload: HuntRequest) -> HuntResult:
    """Run one bounded Outbound Hunter search + scrape cycle."""
    return run_hunt(payload)


@app.post("/hunt/loop", response_model=HuntLoopResultOut, tags=["hunter"])
def hunt_loop(payload: HuntLoopRequest) -> HuntLoopResultOut:
    """Run a bounded branching hunt loop (sync)."""
    if (payload.max_queries == 0 or payload.max_minutes == 0) and not payload.allow_unlimited:
        raise HTTPException(
            status_code=400,
            detail=(
                "unlimited hunt/loop over HTTP requires allow_unlimited=true "
                "(compose CLI workers are unchanged)"
            ),
        )
    settings = get_settings()
    budget = HuntBudget(
        max_queries=payload.max_queries,
        max_minutes=payload.max_minutes,
        max_pages_per_query=payload.max_pages_per_query or settings.hunter_max_pages_per_run,
    )
    result = run_hunt_loop(
        query=payload.query,
        brand=payload.brand,
        budget=budget,
        resume=payload.resume,
        summarize_branches=payload.summarize_branches,
    )
    return HuntLoopResultOut(
        run_id=result.run_id,
        queries_run=result.queries_run,
        resources_found=result.resources_found,
        branch_terms_enqueued=result.branch_terms_enqueued,
        community_terms_enqueued=result.community_terms_enqueued,
        person_terms_enqueued=result.person_terms_enqueued,
        handle_terms_enqueued=result.handle_terms_enqueued,
        engagement_terms_enqueued=result.engagement_terms_enqueued,
        stop_reason=result.stop_reason,
    )


@app.get("/hunt/resources", response_model=list[HuntResourceOut], tags=["hunter"])
def list_hunt_resources(
    brand: Brand | None = None,
    limit: int = 500,
) -> list[HuntResourceOut]:
    rows = HuntStore().list_resources(brand=brand, limit=limit)
    return [HuntResourceOut.model_validate(row) for row in rows]


@app.get("/hunt/queue", response_model=HuntQueueStatusOut, tags=["hunter"])
def hunt_queue_status() -> HuntQueueStatusOut:
    return HuntQueueStatusOut(**HuntStore().queue_status())


@app.get("/hunt/status", response_model=HuntStatusOut, tags=["hunter"])
def hunt_status() -> HuntStatusOut:
    """Live hunt-loop drain status: current query, phase, queue, and email counts."""
    return HuntStatusOut(**build_hunt_status())


# ---- research --------------------------------------------------------------


@app.post("/research", response_model=ResearchResult, tags=["research"])
def research(payload: ResearchRequest) -> ResearchResult:
    """Run a bounded Research agent cycle (competitor, nonprofit, or ad-placement prospecting)."""
    return run_research(payload)


@app.get("/research/findings", response_model=list[ResearchFindingOut], tags=["research"])
def research_findings(
    brand: Brand | None = None,
    kind: ResearchFindingKind | None = None,
    limit: int = 200,
) -> list[ResearchFindingOut]:
    return list_findings(brand=brand, kind=kind, limit=limit)


# ---- treg catalog (paid hunter/research tool allowlist) -------------------


def _treg_tool_out(row) -> TregToolOut:
    return TregToolOut(
        endpoint_id=row.endpoint_id,
        title=row.title,
        summary=row.summary,
        provider=row.provider,
        capability=row.capability,
        platform=row.platform,
        method=row.method,
        queue_as=row.queue_as,
        estimated_cost_usd=row.estimated_cost_usd,
        cost_type=row.cost_type,
        cost_note=row.cost_note,
        is_free=row.is_free,
        is_routed=row.is_routed,
        selectable=row.selectable,
        allowed=row.allowed,
        queued_at=row.queued_at,
    )


@app.get("/treg/status", response_model=TregStatusOut, tags=["treg"])
def treg_status() -> TregStatusOut:
    from agent_crm.treg.client import TregClient, TregError, treg_configured
    from agent_crm.treg.store import treg_counts

    counts = treg_counts()
    settings = get_settings()
    out = TregStatusOut(
        configured=treg_configured(),
        org=settings.treg_org,
        **counts,
    )
    if not treg_configured():
        out.detail = "TREG_API_TOKEN is not set"
        return out
    try:
        with TregClient() as client:
            payload = client.balance()
        out.balance = payload if isinstance(payload, dict) else None
        if isinstance(payload, dict):
            raw = payload.get("balance_usd")
            if raw is None and isinstance(payload.get("balance"), dict):
                raw = payload["balance"].get("usd")
            if raw is None:
                micro = payload.get("balance_micro")
                if micro is not None:
                    raw = float(micro) / 1_000_000
            if raw is not None:
                out.balance_usd = float(raw)
    except TregError as exc:
        out.detail = str(exc)
    return out


@app.post("/treg/catalog/sync", response_model=TregSyncResultOut, tags=["treg"])
def treg_catalog_sync(enqueue_free: bool = True) -> TregSyncResultOut:
    from agent_crm.treg.client import TregError
    from agent_crm.treg.queue import enqueue_free_treg_tools
    from agent_crm.treg.store import sync_treg_catalog

    try:
        result = sync_treg_catalog()
    except TregError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    hunt_enqueued = 0
    research_enqueued = 0
    if enqueue_free:
        queued = enqueue_free_treg_tools()
        hunt_enqueued = queued.hunt_enqueued
        research_enqueued = queued.research_enqueued
    return TregSyncResultOut(
        fetched=result.fetched,
        upserted=result.upserted,
        free=result.free,
        paid_selectable=result.paid_selectable,
        auto_allowed_free=result.auto_allowed_free,
        hunt_enqueued=hunt_enqueued,
        research_enqueued=research_enqueued,
        errors=result.errors,
    )


@app.get("/treg/tools", response_model=list[TregToolOut], tags=["treg"])
def treg_tools(
    paid: bool | None = None,
    selectable: bool | None = None,
    allowed: bool | None = None,
    queue_as: str | None = None,
) -> list[TregToolOut]:
    from agent_crm.treg.store import list_treg_tools

    rows = list_treg_tools(
        paid=paid,
        selectable=selectable,
        allowed=allowed,
        queue_as=queue_as,
    )
    return [_treg_tool_out(row) for row in rows]


@app.post("/treg/tools/allow", response_model=TregAllowResultOut, tags=["treg"])
def treg_tools_allow(body: TregAllowIn) -> TregAllowResultOut:
    from agent_crm.treg.queue import allow_treg_tools

    result = allow_treg_tools(body.endpoint_ids)
    return TregAllowResultOut(
        allowed=result.allowed,
        hunt_enqueued=result.hunt_enqueued,
        research_enqueued=result.research_enqueued,
        skipped=result.skipped,
    )


# ---- agent engagement (comment drafts; publish via /publish/*) ------------


@app.post("/engagement/loop", response_model=EngagementLoopResultOut, tags=["engagement"])
def engagement_loop(payload: EngagementLoopRequest) -> EngagementLoopResultOut:
    """Rescan catalogued forums and draft replies. Does not publish."""
    budget = EngagementBudget(
        max_venues=payload.max_venues,
        max_pages_per_venue=payload.max_pages_per_venue,
        max_minutes=payload.max_minutes,
    )
    result = run_engagement_loop(
        brand=payload.brand,
        budget=budget,
        summarize=payload.summarize,
    )
    return EngagementLoopResultOut(
        venues_scanned=result.venues_scanned,
        threads_cataloged=result.threads_cataloged,
        drafts_written=result.drafts_written,
        pages_scraped=result.pages_scraped,
        errors=result.errors,
        stop_reason=result.stop_reason,
        follow_up_terms_enqueued=result.follow_up_terms_enqueued,
    )


@app.get("/engagement/threads", response_model=list[EngagementThreadOut], tags=["engagement"])
def engagement_threads(
    brand: Brand | None = None,
    limit: int = 200,
) -> list[EngagementThreadOut]:
    rows = list_threads(brand=brand, limit=limit)
    return [EngagementThreadOut.model_validate(row) for row in rows]


@app.get("/engagement/drafts", response_model=list[EngagementDraftOut], tags=["engagement"])
def engagement_drafts(
    brand: Brand | None = None,
    limit: int = 200,
) -> list[EngagementDraftOut]:
    rows = list_drafts(brand=brand, limit=limit)
    return [EngagementDraftOut.model_validate(row) for row in rows]


# ---- publisher (human-scheduled outbound) ---------------------------------


@app.get("/publish/accounts", response_model=list[SocialAccountOut], tags=["publish"])
def publish_accounts(
    brand: Brand | None = None,
    enabled_only: bool = False,
    limit: int = 200,
) -> list[SocialAccountOut]:
    from agent_crm.publish.store import list_social_accounts

    rows = list_social_accounts(brand=brand, enabled_only=enabled_only, limit=limit)
    return [SocialAccountOut.model_validate(row) for row in rows]


@app.post("/publish/accounts", response_model=SocialAccountOut, tags=["publish"])
def publish_accounts_create(payload: SocialAccountCreate) -> SocialAccountOut:
    from agent_crm.publish.store import create_social_account

    try:
        row = create_social_account(
            brand=payload.brand,
            platform=payload.platform,
            handle=payload.handle,
            postiz_integration_id=payload.postiz_integration_id,
            credential_key=payload.credential_key,
            enabled=payload.enabled,
            daily_cap=payload.daily_cap,
            min_interval_minutes=payload.min_interval_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SocialAccountOut.model_validate(row)


@app.get("/publish/jobs", response_model=list[PublishJobOut], tags=["publish"])
def publish_jobs(
    brand: Brand | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[PublishJobOut]:
    from agent_crm.enums import PublishJobStatus
    from agent_crm.publish.store import list_publish_jobs

    status_enum = None
    if status:
        try:
            status_enum = PublishJobStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid status: {status}") from exc
    rows = list_publish_jobs(brand=brand, status=status_enum, limit=limit)
    return [PublishJobOut.model_validate(row) for row in rows]


@app.post("/publish/schedule", response_model=list[PublishJobOut], tags=["publish"])
def publish_schedule(payload: PublishScheduleRequest) -> list[PublishJobOut]:
    from agent_crm.publish.schedule import ScheduleError, schedule_engagement_drafts

    try:
        jobs = schedule_engagement_drafts(
            draft_ids=payload.draft_ids,
            account_id=payload.account_id,
            scheduled_at=payload.scheduled_at,
            use_next_slot=payload.use_next_slot,
            pete_override=payload.pete_override,
            dry_run=payload.dry_run,
        )
    except ScheduleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [PublishJobOut.model_validate(job) for job in jobs]


@app.post(
    "/publish/schedule-package",
    response_model=PublishJobOut,
    tags=["publish"],
)
def publish_schedule_package(payload: ContentPackageScheduleRequest) -> PublishJobOut:
    from agent_crm.publish.schedule import ScheduleError, schedule_content_package
    from agent_crm.publish.store import get_social_account

    account = get_social_account(payload.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="social account not found")
    try:
        job = schedule_content_package(
            source_id=payload.source_id,
            brand=payload.brand,
            account=account,
            body=payload.body,
            scheduled_at=payload.scheduled_at,
            use_next_slot=payload.use_next_slot,
            payload_json=payload.payload_json,
            pete_override=payload.pete_override,
            dry_run=payload.dry_run,
        )
    except ScheduleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PublishJobOut.model_validate(job)


@app.post("/publish/jobs/{job_id}/cancel", response_model=PublishJobOut, tags=["publish"])
def publish_job_cancel(job_id: int) -> PublishJobOut:
    from agent_crm.publish.store import cancel_publish_job

    row = cancel_publish_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="publish job not found")
    return PublishJobOut.model_validate(row)


@app.post("/publish/loop", response_model=PublishLoopResultOut, tags=["publish"])
def publish_loop(payload: PublishLoopRequest) -> PublishLoopResultOut:
    from agent_crm.publish.loop import PublishBudget, run_publish_loop

    result = run_publish_loop(budget=PublishBudget(max_jobs=payload.max_jobs))
    return PublishLoopResultOut(
        claimed=result.claimed,
        posted=result.posted,
        failed=result.failed,
        rescheduled=result.rescheduled,
        skipped=result.skipped,
        errors=result.errors,
        stop_reason=result.stop_reason,
    )


# ---- SEO documents (reviews + plans; never implemented on live sites) ------


@app.post("/seo/loop", response_model=SeoLoopResultOut, tags=["seo"])
def seo_loop(payload: SeoLoopRequest) -> SeoLoopResultOut:
    """Scrape target sites and write SEO review/plan documents. Never implements."""
    budget = SeoBudget(
        max_targets=payload.max_targets,
        max_pages_per_target=payload.max_pages_per_target,
        max_minutes=payload.max_minutes,
    )
    result = run_seo_loop(
        brand=payload.brand,
        budget=budget,
        summarize=payload.summarize,
    )
    return SeoLoopResultOut(
        targets_processed=result.targets_processed,
        reviews_written=result.reviews_written,
        plans_written=result.plans_written,
        pages_scraped=result.pages_scraped,
        errors=result.errors,
        stop_reason=result.stop_reason,
    )


@app.get("/seo/targets", response_model=list[SeoTargetOut], tags=["seo"])
def seo_targets(
    brand: Brand | None = None,
    role: SeoTargetRole | None = None,
    limit: int = 200,
) -> list[SeoTargetOut]:
    rows = list_targets(brand=brand, role=role, limit=limit)
    return [SeoTargetOut.model_validate(row) for row in rows]


@app.get("/seo/reviews", response_model=list[SeoReviewOut], tags=["seo"])
def seo_reviews(
    brand: Brand | None = None,
    kind: SeoReviewKind | None = None,
    status: SeoReviewStatus | None = None,
    limit: int = 200,
) -> list[SeoReviewOut]:
    rows = list_reviews(brand=brand, kind=kind, status=status, limit=limit)
    return [SeoReviewOut.model_validate(row) for row in rows]


@app.post("/aeo-geo/loop", response_model=SeoLoopResultOut, tags=["aeo-geo"])
def aeo_geo_loop(payload: SeoLoopRequest) -> SeoLoopResultOut:
    """Scrape target sites and write AEO/GEO review/plan documents. Never implements."""
    budget = SeoBudget(
        max_targets=payload.max_targets,
        max_pages_per_target=payload.max_pages_per_target,
        max_minutes=payload.max_minutes,
    )
    result = run_aeo_geo_loop(
        brand=payload.brand,
        budget=budget,
        summarize=payload.summarize,
    )
    return SeoLoopResultOut(
        targets_processed=result.targets_processed,
        reviews_written=result.reviews_written,
        plans_written=result.plans_written,
        pages_scraped=result.pages_scraped,
        errors=result.errors,
        stop_reason=result.stop_reason,
    )


@app.get("/seo/plans", response_model=list[SeoPlanOut], tags=["seo"])
def seo_plans(
    brand: Brand | None = None,
    kind: SeoPlanKind | None = None,
    status: SeoPlanStatus | None = None,
    limit: int = 200,
) -> list[SeoPlanOut]:
    rows = list_plans(brand=brand, kind=kind, status=status, limit=limit)
    return [SeoPlanOut.model_validate(row) for row in rows]


# ---- contacts --------------------------------------------------------------


@app.get("/contacts", response_model=list[ContactProfileOut], tags=["contacts"])
def list_contacts(
    response: Response,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    email: str | None = None,
    quality: str | None = None,
    person_only: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[ContactProfileOut]:
    """List contact profiles keyed by email."""
    from agent_crm.contacts.quality import EmailQualityFilter

    resolved_quality: EmailQualityFilter = "person" if person_only else "all"
    if quality in ("person", "role", "all"):
        resolved_quality = quality
    total = count_contact_profiles(
        brand=brand,
        audience=audience,
        email=email,
        quality=resolved_quality,
    )
    response.headers["X-Total-Count"] = str(total)
    return list_contact_profiles(
        brand=brand,
        audience=audience,
        email=email,
        quality=resolved_quality,
        offset=offset,
        limit=limit,
    )


@app.get("/contacts/summary", response_model=ContactProfilesSummaryOut, tags=["contacts"])
def contacts_summary(
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    quality: str | None = None,
    person_only: bool = False,
) -> ContactProfilesSummaryOut:
    """Return total and per-brand counts for contact profile filters."""
    from agent_crm.contacts.quality import EmailQualityFilter

    resolved_quality: EmailQualityFilter = "person" if person_only else "all"
    if quality in ("person", "role", "all"):
        resolved_quality = quality
    total = count_contact_profiles(
        brand=brand,
        audience=audience,
        quality=resolved_quality,
    )
    if brand is None:
        by_brand = [
            {"brand": row["brand"], "count": row["count"]}
            for row in count_contact_profiles_by_brand(audience=audience)
        ]
    else:
        by_brand = [{"brand": brand, "count": total}]
    return ContactProfilesSummaryOut(total=total, by_brand=by_brand)


@app.post("/contacts/backfill", response_model=ContactBackfillResultOut, tags=["contacts"])
def contacts_backfill(body: ContactBackfillRequest) -> ContactBackfillResultOut:
    """Re-apply contact-quality filters to existing profiles and hunt notes."""
    return backfill_contact_quality(limit=body.limit, dry_run=body.dry_run)


@app.post("/contacts/enrich", response_model=ContactEnrichResultOut, tags=["contacts"])
def contacts_enrich(body: ContactEnrichRequest) -> ContactEnrichResultOut:
    """Backfill public people-enrichment for profiles missing enrichment data."""
    return backfill_contact_enrichment(limit=body.limit, dry_run=body.dry_run)


@app.get("/comment-people", response_model=list[CommentPersonOut], tags=["contacts"])
def list_comment_people_endpoint(
    response: Response,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    platform: str | None = None,
    offset: int = 0,
    limit: int = 500,
) -> list[CommentPersonOut]:
    """List handle-keyed comment authors collected from scraped threads."""
    total = count_comment_people(brand=brand, audience=audience, platform=platform)
    response.headers["X-Total-Count"] = str(total)
    return list_comment_people(
        brand=brand,
        audience=audience,
        platform=platform,
        offset=offset,
        limit=limit,
    )


@app.get("/jobs/status", tags=["jobs"])
def jobs_status() -> dict:
    """Return pending/running agent job counts by kind."""
    from agent_crm.jobs.dispatcher import build_job_status

    return build_job_status()


@app.get(
    "/improvement-notes",
    response_model=list[ImprovementNoteOut],
    tags=["orchestrator"],
)
def improvement_notes(
    status: ImprovementNoteStatus | None = ImprovementNoteStatus.OPEN,
    limit: int = 200,
) -> list[ImprovementNoteOut]:
    """List self-learning improvement notes for Manager/Cursor follow-up."""
    return list_improvement_notes(status=status, limit=limit)


# ---- lead verifier ---------------------------------------------------------


@app.post(
    "/leads/{lead_id}/verify",
    response_model=list[ContactVerificationOut],
    tags=["verifier"],
)
def verify_lead_endpoint(lead_id: int) -> list[ContactVerificationOut]:
    """Verify all extractable contacts on a lead (DNS/MX/HTTP — no mail sent)."""
    try:
        return verify_lead(lead_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/leads/{lead_id}/verifications",
    response_model=list[ContactVerificationOut],
    tags=["verifier"],
)
def get_lead_verifications(lead_id: int) -> list[ContactVerificationOut]:
    try:
        return list_verifications(lead_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/verify/batch", response_model=BatchVerifyResult, tags=["verifier"])
def verify_batch_endpoint(body: BatchVerifyRequest) -> BatchVerifyResult:
    """Verify unverified hunter leads, capped at ``limit``."""
    return verify_batch_unverified(limit=body.limit)


@app.post("/verify/raw", response_model=VerifyRawResult, tags=["verifier"])
def verify_raw_endpoint(body: VerifyRawRequest) -> VerifyRawResult:
    """Verify a raw email or URL without a lead record."""
    if not body.email and not body.url:
        raise HTTPException(status_code=400, detail="email or url required")
    return verify_raw(body)


# ---- reads -----------------------------------------------------------------


@app.get("/leads", response_model=list[LeadOut], tags=["leads"])
def list_leads(limit: int = 100) -> list[LeadOut]:
    crm = CRMToolkit(actor="api")
    return crm.list_leads(limit=limit)


@app.get("/leads/{lead_id}", response_model=LeadOut, tags=["leads"])
def get_lead(lead_id: int) -> LeadOut:
    crm = CRMToolkit(actor="api")
    try:
        return crm.get_lead(lead_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/leads/{lead_id}/activities",
    response_model=list[ActivityOut],
    tags=["leads"],
)
def get_activities(lead_id: int) -> list[ActivityOut]:
    crm = CRMToolkit(actor="api")
    try:
        return crm.list_activities(lead_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---- pipeline (Pipeline Manager) -------------------------------------------


@app.post(
    "/leads/{lead_id}/stage",
    response_model=OpportunityOut,
    tags=["pipeline"],
)
def change_stage(lead_id: int, body: StageChangeIn) -> OpportunityOut:
    pm = PipelineManager(actor="api")
    try:
        return pm.transition(lead_id, body.to_stage, note=body.note)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidStageTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/report/weekly", tags=["analytics"])
def weekly_report() -> dict:
    return PipelineManager(actor="analytics").weekly_report()


@app.get("/report/growth", response_model=CatalogGrowthOut, tags=["analytics"])
def catalog_growth_report(
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
) -> CatalogGrowthOut:
    """New emails, names, companies, websites, and related fields in 1h / 4h / 24h."""
    return CatalogGrowthOut.model_validate(
        catalog_growth(brand=brand, audience=audience)
    )


# ---- agent presence / observer ---------------------------------------------


@app.post("/agents/{agent_name}/heartbeat", response_model=HeartbeatOut, tags=["agents"])
def agent_heartbeat(agent_name: str, body: HeartbeatIn) -> HeartbeatOut:
    require_known_agent(agent_name)
    snapshot = record_heartbeat(
        agent_name,
        status=body.status,
        task=body.task,
        resource=body.resource,
        metadata=body.metadata,
    )
    return HeartbeatOut(
        agent_name=snapshot.agent_name,
        status=snapshot.status,
        task=snapshot.task,
        resource=snapshot.resource,
        metadata=body.metadata,
        last_seen_at=snapshot.last_seen_at,
    )


@app.get("/agents", response_model=list[AgentObserverOut], tags=["agents"])
def list_agents() -> list[AgentObserverOut]:
    queue_health = fetch_spark_queue_health()
    rows = build_observer_rows(list_heartbeats(), queue_health)
    assignments = list_assignments_by_agent()
    return [
        AgentObserverOut(
            name=row.name,
            display_name=row.display_name,
            status=row.status,
            task=row.task,
            resource=row.resource,
            last_heartbeat=row.last_heartbeat,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            saved_usd=row.saved_usd,
            tokens_per_hour=row.tokens_per_hour,
            enabled=row.enabled,
            placeholder=row.placeholder,
            toggleable=row.toggleable,
            skills=assignments.get(row.name, []),
        )
        for row in rows
    ]


@app.put("/agents/{agent_name}/enabled", tags=["agents"])
def set_agent_enabled_flag(agent_name: str, body: AgentEnabledIn) -> dict:
    enabled = set_agent_enabled(agent_name, body.enabled)
    return {"name": agent_name, "enabled": enabled}


@app.get("/agents/spark", response_model=SparkSlotSummaryOut, tags=["agents"])
def spark_resources() -> SparkSlotSummaryOut:
    return SparkSlotSummaryOut.model_validate(spark_slot_summary(fetch_spark_queue_health()))


@app.get("/queues", response_model=QueuesOut, tags=["agents"])
def list_work_queues() -> QueuesOut:
    """Pending hunt/research/engagement/SEO/job counts for the Live Agents rail."""
    payload = build_queue_lanes()
    return QueuesOut(
        waiting=payload["waiting"],
        lanes=[QueueLaneOut.model_validate(lane) for lane in payload["lanes"]],
    )


@app.get("/skills", response_model=SkillsOut, tags=["skills"])
def list_skills() -> SkillsOut:
    """Vendored skill catalog plus which agents currently have each skill."""
    return SkillsOut(
        skills=[SkillCatalogItemOut.model_validate(item) for item in catalog_with_usage()]
    )


@app.get("/agents/{agent_name}/skills", response_model=AgentSkillsOut, tags=["skills"])
def get_agent_skills(agent_name: str) -> AgentSkillsOut:
    try:
        skills = list_agent_skills(agent_name)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AgentSkillsOut(name=agent_name, skills=skills)


@app.post("/agents/{agent_name}/skills", response_model=AgentSkillsOut, tags=["skills"])
def add_agent_skill(agent_name: str, body: AgentSkillIn) -> AgentSkillsOut:
    try:
        skills = assign_skill(agent_name, body.skill_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AgentSkillsOut(name=agent_name, skills=skills)


@app.delete("/agents/{agent_name}/skills", response_model=AgentSkillsOut, tags=["skills"])
def remove_agent_skill(
    agent_name: str,
    skill_id: str = Query(..., min_length=1, max_length=128),
) -> AgentSkillsOut:
    try:
        skills = unassign_skill(agent_name, skill_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AgentSkillsOut(name=agent_name, skills=skills)


@app.delete("/skills/assignments", tags=["skills"])
def remove_skill_assignments(
    skill_id: str = Query(..., min_length=1, max_length=128),
) -> dict:
    """Unassign a skill from every agent. Does not delete vendored files."""
    try:
        removed = unassign_skill_everywhere(skill_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"skill_id": skill_id, "removed": removed}


@app.post("/agency/requests", response_model=AgencyRequestOut, tags=["agency"])
def submit_agency_request(body: AgencyRequestIn) -> AgencyRequestOut:
    """Queue an operator command for the orchestrator."""
    row = create_agency_request(body.message)
    return AgencyRequestOut.model_validate(row)


@app.get("/agency/requests", response_model=list[AgencyRequestOut], tags=["agency"])
def list_agency_request_history(limit: int = 50) -> list[AgencyRequestOut]:
    """Recent operator commands and orchestrator replies."""
    rows = list_agency_requests(limit=limit)
    return [AgencyRequestOut.model_validate(row) for row in rows]


@app.get("/agency/settings", response_model=list[RuntimeSettingMetaOut], tags=["agency"])
def list_agency_settings() -> list[RuntimeSettingMetaOut]:
    """Dashboard runtime settings (effective values + env defaults)."""
    return [RuntimeSettingMetaOut.model_validate(row) for row in list_runtime_settings_meta()]


@app.put("/agency/settings", response_model=list[RuntimeSettingMetaOut], tags=["agency"])
def update_agency_settings(body: AgencySettingsUpdateIn) -> list[RuntimeSettingMetaOut]:
    """Persist dashboard overrides for ranch infrastructure tuning."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return list_agency_settings()
    update_runtime_settings(updates)
    return list_agency_settings()


@app.post("/agency/settings/probe-spark", response_model=SparkProbeOut, tags=["agency"])
def probe_agency_spark(url: str | None = Query(default=None)) -> SparkProbeOut:
    """Test Spark SGLang reachability from the API container."""
    return SparkProbeOut.model_validate(probe_spark_upstream(url))


# ---- Hermes read-only query API --------------------------------------------


@app.get("/agent/catalog", response_model=AgentCatalogOut, tags=["hermes"])
def hermes_catalog() -> AgentCatalogOut:
    """Discover collections and enum values Hermes can query."""
    return agent_catalog()


@app.get("/agent/search", response_model=AgentSearchOut, tags=["hermes"])
def hermes_search(q: str = Query(..., min_length=1, max_length=200)) -> AgentSearchOut:
    """Federated search across contacts, websites, findings, and comment people."""
    return agent_search(q)


@app.get("/agent/contacts", response_model=AgentPageOut, tags=["hermes"])
def hermes_contacts(
    q: str | None = None,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    quality: str | None = None,
    verified: bool | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> AgentPageOut:
    resolved = quality if quality in ("person", "role", "all") else "all"
    return query_contacts(
        q=q,
        brand=brand,
        audience=audience,
        quality=resolved,  # type: ignore[arg-type]
        verified=verified,
        offset=offset,
        limit=limit,
    )


@app.get("/agent/contacts/{contact_id}", response_model=ContactProfileOut, tags=["hermes"])
def hermes_contact_detail(contact_id: int) -> ContactProfileOut:
    row = get_contact(contact_id)
    if row is None:
        raise HTTPException(status_code=404, detail="contact not found")
    return row


@app.get("/agent/websites", response_model=AgentPageOut, tags=["hermes"])
def hermes_websites(
    q: str | None = None,
    brand: Brand | None = None,
    kind: HuntResourceKind | None = None,
    domain: str | None = None,
    url: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> AgentPageOut:
    return query_websites(
        q=q,
        brand=brand,
        kind=kind,
        domain=domain,
        url=url,
        offset=offset,
        limit=limit,
    )


@app.get("/agent/websites/{resource_id}", response_model=HuntResourceOut, tags=["hermes"])
def hermes_website_detail(resource_id: int) -> HuntResourceOut:
    row = get_website(resource_id)
    if row is None:
        raise HTTPException(status_code=404, detail="website not found")
    return row


@app.get("/agent/findings", response_model=AgentPageOut, tags=["hermes"])
def hermes_findings(
    q: str | None = None,
    brand: Brand | None = None,
    kind: ResearchFindingKind | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> AgentPageOut:
    return query_findings(q=q, brand=brand, kind=kind, offset=offset, limit=limit)


@app.get("/agent/findings/{finding_id}", response_model=ResearchFindingOut, tags=["hermes"])
def hermes_finding_detail(finding_id: int) -> ResearchFindingOut:
    row = get_finding(finding_id)
    if row is None:
        raise HTTPException(status_code=404, detail="finding not found")
    return row


@app.get("/agent/comment-people", response_model=AgentPageOut, tags=["hermes"])
def hermes_comment_people(
    q: str | None = None,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    platform: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> AgentPageOut:
    return query_comment_people(
        q=q,
        brand=brand,
        audience=audience,
        platform=platform,
        offset=offset,
        limit=limit,
    )


@app.get("/agent/pipeline-leads", response_model=AgentPageOut, tags=["hermes"])
def hermes_pipeline_leads(
    q: str | None = None,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> AgentPageOut:
    return query_pipeline_leads(
        q=q,
        brand=brand,
        audience=audience,
        offset=offset,
        limit=limit,
    )


@app.get("/agent/engagement-threads", response_model=AgentPageOut, tags=["hermes"])
def hermes_engagement_threads(
    q: str | None = None,
    brand: Brand | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> AgentPageOut:
    return query_engagement_threads(q=q, brand=brand, offset=offset, limit=limit)


@app.get("/agent/seo-reviews", response_model=AgentPageOut, tags=["hermes"])
def hermes_seo_reviews(
    q: str | None = None,
    brand: Brand | None = None,
    kind: SeoReviewKind | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> AgentPageOut:
    return query_seo_reviews(q=q, brand=brand, kind=kind, offset=offset, limit=limit)


@app.get("/agent/seo-plans", response_model=AgentPageOut, tags=["hermes"])
def hermes_seo_plans(
    q: str | None = None,
    brand: Brand | None = None,
    kind: SeoPlanKind | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> AgentPageOut:
    return query_seo_plans(q=q, brand=brand, kind=kind, offset=offset, limit=limit)


# ---- projects (YAML prompt origins) ----------------------------------------


def _context_exists(doc) -> bool:
    from pathlib import Path

    from agent_crm.projects.store import brand_for_slug
    from agent_crm.marketing_skill import brand_context_path

    if doc.context_file:
        for base in (Path(__file__).resolve().parents[2], Path("/app")):
            candidate = base / doc.context_file
            if candidate.is_file():
                return True
    brand = brand_for_slug(doc.slug)
    if brand is None:
        return False
    path = brand_context_path(brand)
    return path is not None and path.is_file()


def _project_out(doc) -> ProjectOut:
    from agent_crm.projects.schema import CHANNEL_NAMES
    from agent_crm.projects.store import brand_for_slug

    summary = (doc.origin_prompt or "").strip().split("\n", 1)[0][:160]
    channels = {
        name: {"armed": doc.channels[name].armed, "prompt": doc.channels[name].prompt}
        for name in CHANNEL_NAMES
    }
    brand = brand_for_slug(doc.slug)
    return ProjectOut(
        slug=doc.slug,
        name=doc.name,
        status=doc.status.value if hasattr(doc.status, "value") else str(doc.status),
        enabled=doc.enabled,
        site=doc.site,
        alias=doc.alias,
        context_file=doc.context_file,
        context_exists=_context_exists(doc),
        origin_prompt=doc.origin_prompt,
        summary=summary,
        brand=brand,
        channels=channels,
        armed_count=len(doc.armed_channels()),
        channel_count=len(CHANNEL_NAMES),
        seeded_loops=doc.seeded_loops(),
    )


@app.get("/projects", response_model=ProjectsListOut, tags=["projects"])
def projects_list() -> ProjectsListOut:
    from agent_crm.projects import list_projects, projects_stats

    docs = list_projects()
    stats = projects_stats(docs)
    return ProjectsListOut(
        projects=[_project_out(doc) for doc in docs],
        stats=ProjectStatsOut(**stats),
    )


@app.get("/projects/{slug}", response_model=ProjectOut, tags=["projects"])
def projects_get(slug: str) -> ProjectOut:
    from agent_crm.projects import get_project

    try:
        return _project_out(get_project(slug))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationAppError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/projects", response_model=ProjectOut, tags=["projects"])
def projects_create(body: ProjectCreateIn) -> ProjectOut:
    from agent_crm.projects import create_project
    from agent_crm.projects.schema import ProjectStatus

    try:
        status = ProjectStatus(body.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid status: {body.status}") from exc
    try:
        doc = create_project(
            slug=body.slug,
            name=body.name,
            site=body.site,
            origin_prompt=body.origin_prompt,
            alias=body.alias,
            status=status,
            enabled=body.enabled,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationAppError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _project_out(doc)


@app.patch("/projects/{slug}", response_model=ProjectOut, tags=["projects"])
def projects_patch(slug: str, body: ProjectPatchIn) -> ProjectOut:
    from agent_crm.projects import patch_project
    from agent_crm.projects.schema import ProjectStatus

    status = None
    if body.status is not None:
        try:
            status = ProjectStatus(body.status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid status: {body.status}") from exc
    kwargs: dict = {}
    if body.name is not None:
        kwargs["name"] = body.name
    if "site" in body.model_fields_set:
        kwargs["site"] = body.site
    if "alias" in body.model_fields_set:
        kwargs["alias"] = body.alias
    if status is not None:
        kwargs["status"] = status
    if body.origin_prompt is not None:
        kwargs["origin_prompt"] = body.origin_prompt
    if body.enabled is not None:
        kwargs["enabled"] = body.enabled
    if "context_file" in body.model_fields_set:
        kwargs["context_file"] = body.context_file
    try:
        return _project_out(patch_project(slug, **kwargs))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationAppError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/projects/{slug}/channels", response_model=ProjectOut, tags=["projects"])
def projects_put_channels(slug: str, body: ProjectChannelsIn) -> ProjectOut:
    from agent_crm.projects import update_channels

    armed = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return _project_out(update_channels(slug, armed))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationAppError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/projects/{slug}/prompts", response_model=ProjectOut, tags=["projects"])
def projects_put_prompts(slug: str, body: ProjectPromptsIn) -> ProjectOut:
    from agent_crm.projects import update_prompts

    try:
        return _project_out(
            update_prompts(
                slug,
                origin_prompt=body.origin_prompt,
                channel_prompts=body.channels,
            )
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationAppError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/projects/{slug}/reload-context", response_model=ProjectOut, tags=["projects"])
def projects_reload_context(slug: str) -> ProjectOut:
    from agent_crm.projects import reload_context

    try:
        return _project_out(reload_context(slug))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationAppError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
