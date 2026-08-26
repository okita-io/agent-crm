"""Pydantic I/O schemas.

These are the shapes agents pass in and get back. Using Pydantic (not raw ORM
objects) at the tooling boundary means an agent never holds a live DB session
and cannot accidentally mutate the store outside a transaction.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    ActivityType,
    AgentStatus,
    Brand,
    ContactAudience,
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
    max_pages: int = Field(default=50, ge=1, le=250)
    search_limit: int = Field(default=50, ge=1, le=250)
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
    max_queries: int = Field(
        default=0,
        ge=0,
        description="Query budget; 0 or null means unlimited (drain queue).",
    )
    max_minutes: int | None = Field(
        default=0,
        ge=0,
        description="Wall-clock budget in minutes; 0 or null means unlimited.",
    )
    max_pages_per_query: int | None = Field(default=None, ge=1, le=250)
    resume: bool = True
    summarize_branches: bool = True

    @field_validator("max_queries", mode="before")
    @classmethod
    def _coerce_unlimited_max_queries(cls, value: object) -> int:
        if value is None:
            return 0
        return int(value)  # type: ignore[arg-type]

    @field_validator("max_minutes", mode="before")
    @classmethod
    def _coerce_unlimited_max_minutes(cls, value: object) -> int:
        if value is None:
            return 0
        return int(value)  # type: ignore[arg-type]


class HuntLoopResultOut(BaseModel):
    run_id: str
    queries_run: int
    resources_found: int
    branch_terms_enqueued: int
    community_terms_enqueued: int = 0
    person_terms_enqueued: int = 0
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


class HuntRunningQueryOut(BaseModel):
    id: int
    query: str
    brand: Brand
    priority: int
    origin: str
    audience: ContactAudience | None = None
    updated_at: datetime
    running_seconds: int


class HuntQueueBreakdownRow(BaseModel):
    brand: Brand
    priority: int
    status: str
    count: int


class HuntEmailCountRow(BaseModel):
    brand: Brand
    audience: ContactAudience | None = None
    count: int


class HuntCompletedQueryOut(BaseModel):
    id: int
    query: str
    brand: Brand
    updated_at: datetime


class HuntSparkSummaryOut(BaseModel):
    waiting: int
    in_flight: int
    model: str | None = None


class HuntStatusOut(BaseModel):
    phase: str
    now_playing: HuntRunningQueryOut | None = None
    pending: int
    by_status: dict[str, int]
    total_resources: int
    queue_breakdown: list[HuntQueueBreakdownRow]
    email_counts: list[HuntEmailCountRow]
    tactic_studio_email_total: int
    tactic_studio_person_email_total: int | None = None
    tactic_studio_all_email_total: int | None = None
    tactic_studio_email_goal: int
    agent_jobs: dict | None = None
    recently_completed: list[HuntCompletedQueryOut]
    spark: HuntSparkSummaryOut


class ResearchRequest(BaseModel):
    """Research agent run configuration."""

    brand: Brand
    kind: ResearchFindingKind | None = None
    query: str | None = Field(default=None, max_length=500)
    max_queries: int = Field(default=20, ge=1, le=100)
    max_pages: int = Field(default=200, ge=1, le=250)
    max_minutes: int = Field(default=60, ge=1, le=240)
    search_limit: int = Field(default=50, ge=1, le=250)
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


class ContactBackfillRequest(BaseModel):
    """Re-apply contact-quality filters to existing profiles."""

    limit: int = Field(default=500, ge=1, le=5000)
    dry_run: bool = False


class ContactQualityCleanupOut(BaseModel):
    email: str
    kept: bool
    removed_source_urls: list[str] = Field(default_factory=list)
    stripped_social_keys: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class ContactBackfillResultOut(BaseModel):
    profiles_scanned: int
    profiles_updated: int
    profiles_removed: int
    leads_disqualified: int
    resource_notes_scrubbed: int
    details: list[ContactQualityCleanupOut] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ContactEnrichRequest(BaseModel):
    """Backfill public people-enrichment for existing contact profiles."""

    limit: int = Field(default=500, ge=1, le=5000)
    dry_run: bool = False


class ContactEnrichDetailOut(BaseModel):
    email: str
    enriched: bool
    spark_used: bool = False
    fields_filled: list[str] = Field(default_factory=list)


class ContactEnrichResultOut(BaseModel):
    profiles_scanned: int
    profiles_enriched: int
    spark_calls: int
    details: list[ContactEnrichDetailOut] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


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
    audience: ContactAudience | None = None
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


class ContactProfileOut(ORMModel):
    id: int
    email: str
    name: str | None
    brand: Brand
    audience: ContactAudience | None
    socials: dict | None
    source_urls: list[str] | None
    title: str | None
    organization: str | None
    location: str | None
    bio: str | None
    enrichment: dict | None
    lead_id: int | None
    created_at: datetime
    updated_at: datetime


class ContactProfileBrandCountOut(BaseModel):
    brand: Brand
    count: int


class ContactProfilesSummaryOut(BaseModel):
    total: int
    by_brand: list[ContactProfileBrandCountOut]


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
