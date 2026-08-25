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
from .db import database_kind, init_db
from .enums import Brand, Stage
from .errors import InvalidStageTransition, NotFoundError
from .hunt_store import HuntStore
from .outbound_hunter import HuntBudget, OutboundHunter
from .pipeline import PipelineManager
from .schemas import (
    ActivityOut,
    AgentHeartbeatOut,
    HuntLoopRequest,
    HuntLoopResultOut,
    HuntQueueStatusOut,
    HuntRequest,
    HuntResourceOut,
    LeadCreate,
    LeadOut,
    OpportunityOut,
)
from .tooling import CRMToolkit

app = FastAPI(
    title="Agent CRM",
    version=__version__,
    description="Local, agent-driven CRM. Milestone 1: store + intake + pipeline.",
)


@app.on_event("startup")
def _startup() -> None:
    # Ensure tables exist for a fresh SQLite file. In Postgres/production,
    # Alembic runs first; create_all is a no-op when the schema already exists.
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


# ---- outbound hunter -------------------------------------------------------


@app.post("/hunt", tags=["hunter"])
def hunt_once(body: HuntRequest) -> dict:
    """One-shot prospect search via SearXNG + optional scrape."""
    hunter = OutboundHunter()
    return hunter.hunt_once(
        body.query,
        brand=body.brand,
        max_pages=body.max_pages,
        params=body.params,
    )


@app.post("/hunt/loop", response_model=HuntLoopResultOut, tags=["hunter"])
def hunt_loop(body: HuntLoopRequest) -> HuntLoopResultOut:
    """Run a bounded branching hunt loop (sync)."""
    hunter = OutboundHunter()
    budget = HuntBudget(
        max_queries=body.max_queries,
        max_minutes=body.max_minutes,
        max_pages_per_query=body.max_pages_per_query
        or hunter.settings.hunter_max_pages_per_run,
    )
    result = hunter.hunt_loop(
        query=body.query,
        brand=body.brand,
        budget=budget,
        resume=body.resume,
    )
    return HuntLoopResultOut(
        run_id=result.run_id,
        queries_run=result.queries_run,
        resources_found=result.resources_found,
        leads_created=result.leads_created,
        branch_terms_enqueued=result.branch_terms_enqueued,
        stop_reason=result.stop_reason,
    )


@app.get("/hunt/resources", response_model=list[HuntResourceOut], tags=["hunter"])
def list_hunt_resources(
    brand: Brand | None = None,
    limit: int = 500,
) -> list[HuntResourceOut]:
    store = HuntStore()
    rows = store.list_resources(brand=brand, limit=limit)
    return [HuntResourceOut.model_validate(row) for row in rows]


@app.get("/hunt/queue", response_model=HuntQueueStatusOut, tags=["hunter"])
def hunt_queue_status() -> HuntQueueStatusOut:
    status = HuntStore().queue_status()
    return HuntQueueStatusOut(**status)


@app.get("/hunt/heartbeat", response_model=AgentHeartbeatOut | None, tags=["hunter"])
def hunt_heartbeat() -> AgentHeartbeatOut | None:
    from sqlalchemy import select

    from .db import session_scope
    from .models import AgentHeartbeat

    with session_scope() as session:
        row = session.scalar(
            select(AgentHeartbeat).where(AgentHeartbeat.actor == "outbound_hunter")
        )
        if row is None:
            return None
        return AgentHeartbeatOut.model_validate(row)
