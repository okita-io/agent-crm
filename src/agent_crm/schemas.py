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
    AgentStatus,
    Brand,
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


class HeartbeatIn(BaseModel):
    status: AgentStatus
    task: str | None = None
    resource: str | None = None
    metadata: dict | None = None


class HeartbeatOut(BaseModel):
    agent_name: str
    status: AgentStatus
    task: str | None
    resource: str | None
    metadata: dict | None = None
    last_seen_at: datetime


class AgentObserverOut(BaseModel):
    name: str
    display_name: str
    status: AgentStatus
    task: str | None
    resource: str | None
    last_heartbeat: datetime | None
