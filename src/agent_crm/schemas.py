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
    ContactKind,
    ContactVerificationStatus,
    HuntResourceKind,
    JourneyStatus,
    LeadSource,
    LeadStatus,
    Priority,
    ResearchFindingKind,
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


class HuntRequest(BaseModel):
    """Outbound Hunter run configuration."""

    query: str = Field(min_length=1, max_length=500)
    brand: Brand | None = None
    max_pages: int = Field(default=8, ge=1, le=10)
    search_limit: int = Field(default=15, ge=1, le=25)
    transition_to_prospect: bool = True
    summarize: bool = True


class HuntResult(BaseModel):
    """Summary of one hunt execution."""

    query: str
    brand: Brand | None
    search_results: int
    scraped: int
    leads_created: list[int]
    errors: list[str]


class HuntLoopRequest(BaseModel):
    """Bounded branching hunt loop configuration."""

    query: str | None = None
    brand: Brand = Brand.UNASSIGNED
    max_queries: int = Field(default=20, ge=1, le=200)
    max_minutes: int = Field(default=25, ge=1, le=240)
    max_pages_per_query: int | None = Field(default=None, ge=1, le=10)
    resume: bool = True
    summarize_branches: bool = True


class HuntLoopResultOut(BaseModel):
    run_id: str
    queries_run: int
    resources_found: int
    branch_terms_enqueued: int
    stop_reason: str


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


class ResearchRequest(BaseModel):
    """Research agent run configuration."""

    brand: Brand
    kind: ResearchFindingKind | None = None
    query: str | None = Field(default=None, max_length=500)
    max_queries: int = Field(default=12, ge=1, le=30)
    max_pages: int = Field(default=4, ge=1, le=10)
    max_minutes: int = Field(default=20, ge=1, le=120)
    search_limit: int = Field(default=15, ge=1, le=25)
    summarize: bool = True
    write_accounts: bool = True


class ResearchResult(BaseModel):
    """Summary of one research run."""

    brand: Brand
    kind: ResearchFindingKind
    queries_run: int
    pages_scraped: int
    findings_written: list[int]
    errors: list[str]


class VerifyRawRequest(BaseModel):
    """Verify a raw email or URL without an existing lead."""

    email: str | None = None
    url: str | None = None


class BatchVerifyRequest(BaseModel):
    """Batch-verify unverified hunter leads."""

    limit: int = Field(default=50, ge=1, le=200)


class ContactVerificationOut(ORMModel):
    id: int
    lead_id: int | None
    contact: str
    contact_kind: ContactKind
    status: ContactVerificationStatus
    reasons: list[str] | None
    checked_at: datetime
    dns_summary: dict | None
    mx_summary: dict | None
    http_status: int | None
    created_at: datetime
    updated_at: datetime


class BatchVerifyResult(BaseModel):
    leads_processed: int
    contacts_verified: int
    lead_ids: list[int]
    errors: list[str]


class VerifyRawResult(BaseModel):
    results: list[ContactVerificationOut]


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


class ResearchFindingOut(ORMModel):
    id: int
    url: str
    domain: str
    title: str
    brand: Brand
    kind: ResearchFindingKind
    summary: str
    source_query: str
    raw_snippet: str | None
    extra: dict | None
    first_seen_at: datetime
    last_seen_at: datetime
