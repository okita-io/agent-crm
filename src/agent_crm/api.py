"""The FastAPI service.

Milestone 1 surface:
- ``GET  /health``            -- liveness + which store is attached
- ``POST /intake/webhook``    -- the Inbound Listener write path (form/DM/email -> row)
- ``GET  /leads``             -- list leads (dashboard / debugging)
- ``GET  /leads/{id}``        -- one lead
- ``GET  /leads/{id}/activities`` -- append-only history
- ``POST /leads/{id}/stage``  -- Pipeline Manager stage transition
- ``GET  /report/weekly``     -- Analytics weekly snapshot

The API is a thin shell over the tooling + Pipeline Manager. It does not embed
business logic so the same operations work from an agent process without HTTP.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel

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
from .comment_people_store import count_comment_people, list_comment_people
from .config import get_settings
from .contact_store import (
    backfill_contact_enrichment,
    backfill_contact_quality,
    count_contact_profiles,
    count_contact_profiles_by_brand,
    list_contact_profiles,
)
from .db import database_kind, init_db
from .engagement_loop import EngagementBudget, run_engagement_loop
from .engagement_store import list_drafts, list_threads
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
from .agency_request_store import create_agency_request, list_agency_requests
from .runtime_settings_store import (
    list_runtime_settings_meta,
    probe_spark_upstream,
    update_runtime_settings,
)
from .heartbeat import list_heartbeats, record_heartbeat
from .hunt_loop import HuntBudget, run_hunt_loop
from .hunt_status import build_hunt_status
from .hunt_store import HuntStore
from .improvement_store import list_improvement_notes
from .outbound_hunter import run_hunt
from .pipeline import PipelineManager
from .presence import build_observer_rows, fetch_spark_queue_health, spark_slot_summary
from .research import run_research
from .research_store import list_findings
from .schemas import (
    ActivityOut,
    AgentCatalogOut,
    AgentEnabledIn,
    AgentObserverOut,
    AgencyRequestIn,
    AgencyRequestOut,
    AgencySettingsUpdateIn,
    AgentPageOut,
    AgentSearchOut,
    BatchVerifyRequest,
    BatchVerifyResult,
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
    ResearchFindingOut,
    ResearchRequest,
    ResearchResult,
    RuntimeSettingMetaOut,
    SeoLoopRequest,
    SeoLoopResultOut,
    SeoPlanOut,
    SeoReviewOut,
    SeoTargetOut,
    SparkProbeOut,
    VerifyRawRequest,
    VerifyRawResult,
)
from .aeo_geo_loop import run_aeo_geo_loop
from .seo_loop import SeoBudget, run_seo_loop
from .seo_store import list_plans, list_reviews, list_targets
from .tooling import CRMToolkit
from .verifier import list_verifications, verify_batch_unverified, verify_lead, verify_raw

app = FastAPI(
    title="The Agency",
    version=__version__,
    description=(
        "The Agency — local, agent-driven CRM plus SEO and AEO/GEO document workflows "
        "(never applied to live sites). Repository: okita-io/agent-crm."
    ),
    dependencies=[Depends(require_api_token)],
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


# ---- agent engagement (comment drafts; never posts) -----------------------


@app.post("/engagement/loop", response_model=EngagementLoopResultOut, tags=["engagement"])
def engagement_loop(payload: EngagementLoopRequest) -> EngagementLoopResultOut:
    """Rescan catalogued forums and draft replies. This stack never posts."""
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
    from .contact_quality import EmailQualityFilter

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
    from .contact_quality import EmailQualityFilter

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
    from .job_dispatcher import build_job_status

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
        )
        for row in rows
    ]


@app.put("/agents/{agent_name}/enabled", tags=["agents"])
def set_agent_enabled_flag(agent_name: str, body: AgentEnabledIn) -> dict:
    enabled = set_agent_enabled(agent_name, body.enabled)
    return {"name": agent_name, "enabled": enabled}


@app.get("/agents/spark", tags=["agents"])
def spark_resources() -> dict:
    return spark_slot_summary(fetch_spark_queue_health())


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
