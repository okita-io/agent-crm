"""Pydantic I/O schemas.

These are the shapes agents pass in and get back. Using Pydantic (not raw ORM
objects) at the tooling boundary means an agent never holds a live DB session
and cannot accidentally mutate the store outside a transaction.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    ActivityType,
    AgentHeartbeatStatus,
    Brand,
    HuntResourceKind,
    JourneyStatus,
    LeadSource,
    LeadStatus,
    Priority,
    Stage,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- Inputs ----------------------------------------------------------------


class LeadCreate(BaseModel):
    """Payload Lead Intake uses to create a lead."""

    name: str | None = None
    email: str | None = None
    company: str | None = None
    source: LeadSource
    raw_payload: dict | None = None


class ScoreInput(BaseModel):
    """Payload the Lead Scorer writes back."""

    score: int = Field(ge=0, le=100)
    priority: Priority


class EnrichmentInput(BaseModel):
    """Payload the Research agent writes back."""

    summary: str
    website: str | None = None
    socials: dict | None = None


# ---- Outputs ---------------------------------------------------------------


class ActivityOut(ORMModel):
    id: int
    lead_id: int | None
    opportunity_id: int | None
    actor: str
    type: ActivityType
    summary: str
    payload: dict | None
    created_at: datetime


class OpportunityOut(ORMModel):
    id: int
    lead_id: int
    account_id: int | None
    stage: Stage
    brand: Brand
    amount: float | None
    next_action_at: datetime | None
    is_hot: bool
    created_at: datetime
    updated_at: datetime


class AccountOut(ORMModel):
    id: int
    name: str
    website: str | None
    socials: dict | None
    notes: str | None


class LeadOut(ORMModel):
    id: int
    name: str | None
    email: str | None
    company: str | None
    source: LeadSource
    score: int | None
    priority: Priority | None
    brand: Brand
    enrichment_summary: str | None
    status: LeadStatus
    account_id: int | None
    created_at: datetime
    updated_at: datetime


class JourneyOut(ORMModel):
    id: int
    lead_id: int
    template_set: str
    brand: Brand
    step_index: int
    next_run_at: datetime | None
    status: JourneyStatus
    stop_reason: str | None


# ---- Hunter ----------------------------------------------------------------


class HuntRequest(BaseModel):
    query: str
    brand: Brand = Brand.UNASSIGNED
    max_pages: int | None = Field(default=None, ge=0, le=50)
    params: dict | None = None


class HuntLoopRequest(BaseModel):
    query: str | None = None
    brand: Brand = Brand.UNASSIGNED
    max_queries: int = Field(default=20, ge=1, le=200)
    max_minutes: int = Field(default=25, ge=1, le=240)
    max_pages_per_query: int | None = Field(default=None, ge=0, le=50)
    resume: bool = True


class HuntResourceOut(ORMModel):
    id: int
    url: str
    domain: str
    title: str | None
    brand: Brand
    kind: HuntResourceKind
    found_via_query: str | None
    first_seen: datetime
    last_seen: datetime
    hit_count: int
    notes: str | None


class HuntQueueStatusOut(BaseModel):
    pending: int
    by_status: dict[str, int]
    total_resources: int


class HuntLoopResultOut(BaseModel):
    run_id: str
    queries_run: int
    resources_found: int
    leads_created: int
    branch_terms_enqueued: int
    stop_reason: str


class AgentHeartbeatOut(ORMModel):
    actor: str
    status: AgentHeartbeatStatus
    message: str | None
    updated_at: datetime
