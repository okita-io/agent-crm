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

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import __version__
from .config import get_settings
from .contact_store import list_contact_profiles
from .db import database_kind, init_db
from .enums import Brand, ResearchFindingKind, Stage
from .errors import InvalidStageTransition, NotFoundError
from .heartbeat import list_heartbeats, record_heartbeat
from .hunt_loop import HuntBudget, run_hunt_loop
from .hunt_store import HuntStore
from .outbound_hunter import run_hunt
from .pipeline import PipelineManager
from .presence import build_observer_rows, fetch_spark_queue_health, spark_slot_summary
from .research import run_research
from .research_store import list_findings
from .schemas import (
    ActivityOut,
    AgentObserverOut,
    BatchVerifyRequest,
    BatchVerifyResult,
    ContactVerificationOut,
    ContactProfileOut,
    HeartbeatIn,
    HeartbeatOut,
    HuntLoopRequest,
    HuntLoopResultOut,
    HuntQueueStatusOut,
    HuntRequest,
    HuntResourceOut,
    HuntResult,
    LeadCreate,
    LeadOut,
    OpportunityOut,
    ResearchFindingOut,
    ResearchRequest,
    ResearchResult,
    VerifyRawRequest,
    VerifyRawResult,
)
from .tooling import CRMToolkit
from .verifier import list_verifications, verify_batch_unverified, verify_lead, verify_raw

app = FastAPI(
    title="Agent CRM",
    version=__version__,
    description="Local, agent-driven CRM. Milestone 1: store + intake + pipeline.",
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


@app.get("/health", response_model=HealthOut, tags=["system"])
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


# ---- research --------------------------------------------------------------


@app.post("/research", response_model=ResearchResult, tags=["research"])
def research(payload: ResearchRequest) -> ResearchResult:
    """Run a bounded Research agent cycle (competitor or nonprofit prospecting)."""
    return run_research(payload)


@app.get("/research/findings", response_model=list[ResearchFindingOut], tags=["research"])
def research_findings(
    brand: Brand | None = None,
    kind: ResearchFindingKind | None = None,
    limit: int = 200,
) -> list[ResearchFindingOut]:
    return list_findings(brand=brand, kind=kind, limit=limit)


# ---- contacts --------------------------------------------------------------


@app.get("/contacts", response_model=list[ContactProfileOut], tags=["contacts"])
def list_contacts(
    brand: Brand | None = None,
    email: str | None = None,
    limit: int = 500,
) -> list[ContactProfileOut]:
    """List contact profiles keyed by email."""
    return list_contact_profiles(brand=brand, email=email, limit=limit)


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
        )
        for row in rows
    ]


@app.get("/agents/spark", tags=["agents"])
def spark_resources() -> dict:
    return spark_slot_summary(fetch_spark_queue_health())
