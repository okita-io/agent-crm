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
from .enums import Stage
from .errors import InvalidStageTransition, NotFoundError
from .heartbeat import list_heartbeats, record_heartbeat
from .pipeline import PipelineManager
from .presence import build_observer_rows, fetch_spark_queue_health, spark_slot_summary
from .schemas import (
    ActivityOut,
    AgentObserverOut,
    HeartbeatIn,
    HeartbeatOut,
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
