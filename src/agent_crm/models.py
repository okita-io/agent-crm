"""The CRM data model.

This mirrors the "Intended data, in one page" section of the brief:

    Lead        -- person at the top of funnel
    Account     -- company or project home
    Opportunity -- a lead that is in play
    Activity    -- append-only history of everything agents do
    Journey     -- a nurture state-machine instance

The model is deliberately DB-agnostic: JSON columns and UTC timestamps behave
the same on SQLite (dev) and Postgres (the NAS target).
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .enums import (
    ActivityType,
    AgencyRequestStatus,
    AgentJobKind,
    AgentJobStatus,
    AgentStatus,
    Brand,
    ContactAudience,
    ContactEmailKind,
    ContactKind,
    ContactVerificationStatus,
    EngagementDraftStatus,
    EngagementQueryStatus,
    EngagementThreadStatus,
    HuntPageType,
    HuntQueryStatus,
    HuntResourceKind,
    ImprovementNoteKind,
    ImprovementNoteSeverity,
    ImprovementNoteStatus,
    ImprovementSourceAgent,
    JourneyStatus,
    LeadSource,
    LeadStatus,
    Priority,
    ResearchFindingKind,
    ResearchQueryStatus,
    SeoPlanKind,
    SeoPlanStatus,
    SeoQueryKind,
    SeoQueryStatus,
    SeoReviewKind,
    SeoReviewStatus,
    SeoTargetRole,
    Stage,
    TopicalRelevanceVerdict,
)


def utcnow() -> datetime:
    """Timezone-aware UTC now. Used as the default for every timestamp."""
    return datetime.now(UTC)


def _str_enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Return enum member values for Postgres native enum labels."""
    return [member.value for member in enum_cls]


def str_enum(enum_cls: type[enum.Enum], **kwargs) -> SAEnum:
    """SAEnum that persists ``str`` enum values (e.g. ``thinking``), not names."""
    return SAEnum(enum_cls, values_callable=_str_enum_values, **kwargs)


def existing_brand_enum(**kwargs) -> SAEnum:
    """Reuse the Postgres ``brand`` enum created by the initial schema."""
    return SAEnum(Brand, name="brand", create_type=False, **kwargs)


def existing_topical_verdict_enum(**kwargs) -> SAEnum:
    """Reuse the Postgres ``topicalrelevanceverdict`` enum from Alembic."""
    return SAEnum(
        TopicalRelevanceVerdict,
        name="topicalrelevanceverdict",
        values_callable=_str_enum_values,
        create_type=False,
        **kwargs,
    )


class Base(DeclarativeBase):
    """Declarative base for all CRM tables."""


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Account(Base, TimestampMixin):
    """A company or project home. Enriched by the Research agent."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    socials: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    leads: Mapped[list[Lead]] = relationship(back_populates="account")
    opportunities: Mapped[list[Opportunity]] = relationship(back_populates="account")


class Lead(Base, TimestampMixin):
    """A person at the top of the funnel. Written first by Lead Intake."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Identity
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Capture
    source: Mapped[LeadSource] = mapped_column(SAEnum(LeadSource), nullable=False)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Scoring (Lead Scorer)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[Priority | None] = mapped_column(SAEnum(Priority), nullable=True)

    # Routing (Brand Router)
    brand: Mapped[Brand] = mapped_column(
        SAEnum(Brand), default=Brand.UNASSIGNED, nullable=False
    )
    audience: Mapped[ContactAudience | None] = mapped_column(
        str_enum(ContactAudience), nullable=True, index=True
    )

    # Enrichment (Research)
    enrichment_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Lifecycle
    status: Mapped[LeadStatus] = mapped_column(
        SAEnum(LeadStatus), default=LeadStatus.NEW, nullable=False
    )

    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )

    account: Mapped[Account | None] = relationship(back_populates="leads")
    opportunity: Mapped[Opportunity | None] = relationship(
        back_populates="lead", uselist=False
    )
    activities: Mapped[list[Activity]] = relationship(
        back_populates="lead", order_by="Activity.created_at"
    )
    journeys: Mapped[list[Journey]] = relationship(back_populates="lead")
    contact_verifications: Mapped[list[ContactVerification]] = relationship(
        back_populates="lead", order_by="ContactVerification.checked_at"
    )
    contact_profile: Mapped[ContactProfile | None] = relationship(
        back_populates="lead", uselist=False
    )


class Opportunity(Base, TimestampMixin):
    """A lead that is in play. Owned by the CRM/Pipeline Manager."""

    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )

    stage: Mapped[Stage] = mapped_column(
        SAEnum(Stage), default=Stage.NEW, nullable=False, index=True
    )
    brand: Mapped[Brand] = mapped_column(
        SAEnum(Brand), default=Brand.UNASSIGNED, nullable=False
    )
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    next_action_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_hot: Mapped[bool] = mapped_column(Integer, default=0, nullable=False)

    lead: Mapped[Lead] = relationship(back_populates="opportunity")
    account: Mapped[Account | None] = relationship(back_populates="opportunities")


class Activity(Base, TimestampMixin):
    """Append-only history: every send, scrape, score, stage change, human note.

    Agents append; they do not rewrite history.
    """

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=True, index=True
    )
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=True
    )

    # Which agent produced this (e.g. "lead_intake", "lead_scoring", "human").
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[ActivityType] = mapped_column(SAEnum(ActivityType), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    lead: Mapped[Lead | None] = relationship(back_populates="activities")


class AgentHeartbeat(Base):
    """Last-known liveness and task state for a CRM agent actor."""

    __tablename__ = "agent_heartbeats"

    agent_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[AgentStatus] = mapped_column(
        str_enum(AgentStatus), default=AgentStatus.IDLE, nullable=False
    )
    task: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resource: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class AgentToggle(Base):
    """Dashboard switch that pauses or resumes a standing agent worker."""

    __tablename__ = "agent_toggles"

    agent_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class AgencyRequest(Base):
    """Operator message for the orchestrator to interpret and act on."""

    __tablename__ = "agency_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AgencyRequestStatus] = mapped_column(
        str_enum(AgencyRequestStatus),
        default=AgencyRequestStatus.PENDING,
        nullable=False,
    )
    reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    actions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AgencySetting(Base):
    """Dashboard overrides for ranch infrastructure and observer tuning."""

    __tablename__ = "agency_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSON, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class LlmTokenUsage(Base):
    """Lifetime LLM token totals per spark-queue actor (survives restarts)."""

    __tablename__ = "llm_token_usage"

    agent_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class LlmTokenUsageHour(Base):
    """Per-actor token totals bucketed by UTC hour for hourly rate."""

    __tablename__ = "llm_token_usage_hours"

    agent_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    hour_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class HuntQuery(Base, TimestampMixin):
    """Priority queue of search terms for the outbound hunter loop."""

    __tablename__ = "hunt_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    origin: Mapped[str] = mapped_column(String(128), nullable=False, default="seed")
    brand: Mapped[Brand] = mapped_column(existing_brand_enum(), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=30, nullable=False, index=True)
    status: Mapped[HuntQueryStatus] = mapped_column(
        str_enum(HuntQueryStatus),
        default=HuntQueryStatus.PENDING,
        nullable=False,
        index=True,
    )
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class HuntResource(Base, TimestampMixin):
    """Discovered sites where potential users might be found."""

    __tablename__ = "hunt_resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    brand: Mapped[Brand] = mapped_column(
        existing_brand_enum(), nullable=False, index=True
    )
    kind: Mapped[HuntResourceKind] = mapped_column(
        str_enum(HuntResourceKind),
        default=HuntResourceKind.OTHER,
        nullable=False,
    )
    page_type: Mapped[HuntPageType] = mapped_column(
        str_enum(HuntPageType),
        default=HuntPageType.OTHER,
        nullable=False,
        index=True,
    )
    domain_class: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    found_via_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    hit_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    engagement_score: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, index=True
    )
    last_engagement_scan: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_engagement_scan: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class EngagementThread(Base, TimestampMixin):
    """A popular post/thread on a catalogued forum, community, or social venue."""

    __tablename__ = "engagement_threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True, index=True)
    hunt_resource_id: Mapped[int | None] = mapped_column(
        ForeignKey("hunt_resources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    brand: Mapped[Brand] = mapped_column(
        existing_brand_enum(), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    venue_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    popularity_score: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, index=True
    )
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trend_keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    found_via_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[EngagementThreadStatus] = mapped_column(
        str_enum(EngagementThreadStatus),
        default=EngagementThreadStatus.CATALOGED,
        nullable=False,
        index=True,
    )
    last_scanned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_scan_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    drafts: Mapped[list[EngagementDraft]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )


class EngagementDraft(Base, TimestampMixin):
    """A product-related comment draft. Discovery only — never posted by this stack."""

    __tablename__ = "engagement_drafts"
    __table_args__ = (
        UniqueConstraint("thread_id", "brand", name="uq_engagement_drafts_thread_brand"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("engagement_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    brand: Mapped[Brand] = mapped_column(
        existing_brand_enum(), nullable=False, index=True
    )
    draft_text: Mapped[str] = mapped_column(Text, nullable=False)
    product_angle: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[EngagementDraftStatus] = mapped_column(
        str_enum(EngagementDraftStatus),
        default=EngagementDraftStatus.DRAFT,
        nullable=False,
        index=True,
    )

    thread: Mapped[EngagementThread] = relationship(back_populates="drafts")


class ResearchQuery(Base, TimestampMixin):
    """Append-only queue of research search terms. Rows are never deleted."""

    __tablename__ = "research_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(128), nullable=False, default="seed")
    brand: Mapped[Brand] = mapped_column(existing_brand_enum(), nullable=False, index=True)
    kind: Mapped[ResearchFindingKind] = mapped_column(
        str_enum(ResearchFindingKind), nullable=False, index=True
    )
    status: Mapped[ResearchQueryStatus] = mapped_column(
        str_enum(ResearchQueryStatus),
        default=ResearchQueryStatus.PENDING,
        nullable=False,
        index=True,
    )
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class EngagementQuery(Base, TimestampMixin):
    """Append-only queue of engagement search terms. Rows are never deleted."""

    __tablename__ = "engagement_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(128), nullable=False, default="seed")
    brand: Mapped[Brand] = mapped_column(existing_brand_enum(), nullable=False, index=True)
    hunt_resource_id: Mapped[int | None] = mapped_column(
        ForeignKey("hunt_resources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[EngagementQueryStatus] = mapped_column(
        str_enum(EngagementQueryStatus),
        default=EngagementQueryStatus.PENDING,
        nullable=False,
        index=True,
    )
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResearchFinding(Base):
    """First-class research output from the Research agent."""

    __tablename__ = "research_findings"
    __table_args__ = (
        UniqueConstraint("url", "brand", name="uq_research_findings_url_brand"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    brand: Mapped[Brand] = mapped_column(
        existing_brand_enum(), nullable=False, index=True
    )
    kind: Mapped[ResearchFindingKind] = mapped_column(
        str_enum(ResearchFindingKind), nullable=False, index=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_query: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class CommentPerson(Base, TimestampMixin):
    """A public comment author keyed by platform + handle (no email required)."""

    __tablename__ = "comment_people"
    __table_args__ = (
        UniqueConstraint("platform", "handle", name="uq_comment_people_platform_handle"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    handle: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    brand: Mapped[Brand] = mapped_column(
        existing_brand_enum(), nullable=False, index=True
    )
    audience: Mapped[ContactAudience | None] = mapped_column(
        str_enum(ContactAudience), nullable=True, index=True
    )
    source_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    comment_snippets: Mapped[list | None] = mapped_column(JSON, nullable=True)


class ContactProfile(Base, TimestampMixin):
    """A person contact keyed by email, with social links for follow-up."""

    __tablename__ = "contact_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand: Mapped[Brand] = mapped_column(
        existing_brand_enum(), nullable=False, index=True
    )
    audience: Mapped[ContactAudience | None] = mapped_column(
        str_enum(ContactAudience), nullable=True, index=True
    )
    email_kind: Mapped[ContactEmailKind] = mapped_column(
        str_enum(ContactEmailKind),
        default=ContactEmailKind.PERSON,
        nullable=False,
        index=True,
    )
    socials: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    organization: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bio: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enrichment: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )

    lead: Mapped[Lead | None] = relationship(back_populates="contact_profile")


class AgentJob(Base, TimestampMixin):
    """Background CRM work queue (enrichment, verification, Spark decode)."""

    __tablename__ = "agent_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[AgentJobKind] = mapped_column(
        str_enum(AgentJobKind), nullable=False, index=True
    )
    status: Mapped[AgentJobStatus] = mapped_column(
        str_enum(AgentJobStatus),
        default=AgentJobStatus.PENDING,
        nullable=False,
        index=True,
    )
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    actor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ContactVerification(Base, TimestampMixin):
    """Defensive check result for an email or contact URL on a lead."""

    __tablename__ = "contact_verifications"
    __table_args__ = (UniqueConstraint("lead_id", "contact", name="uq_lead_contact"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact: Mapped[str] = mapped_column(String(2048), nullable=False)
    contact_kind: Mapped[ContactKind] = mapped_column(
        str_enum(ContactKind), nullable=False
    )
    status: Mapped[ContactVerificationStatus] = mapped_column(
        str_enum(ContactVerificationStatus), nullable=False
    )
    reasons: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dns_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    mx_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)

    lead: Mapped[Lead] = relationship(back_populates="contact_verifications")


class UrlTopicRelevance(Base, TimestampMixin):
    """Topical relevance verdict for a URL under a specific brand."""

    __tablename__ = "url_topic_relevance"
    __table_args__ = (UniqueConstraint("url", "brand", name="uq_url_topic_brand"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    brand: Mapped[Brand] = mapped_column(existing_brand_enum(), nullable=False, index=True)
    verdict: Mapped[TopicalRelevanceVerdict] = mapped_column(
        existing_topical_verdict_enum(), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    page_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentImprovementNote(Base, TimestampMixin):
    """Self-learning gap/performance note for orchestrator and Cursor follow-up."""

    __tablename__ = "agent_improvement_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[ImprovementNoteKind] = mapped_column(
        str_enum(ImprovementNoteKind), nullable=False, index=True
    )
    severity: Mapped[ImprovementNoteSeverity] = mapped_column(
        str_enum(ImprovementNoteSeverity), nullable=False, index=True
    )
    source_agent: Mapped[ImprovementSourceAgent] = mapped_column(
        str_enum(ImprovementSourceAgent), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ImprovementNoteStatus] = mapped_column(
        str_enum(ImprovementNoteStatus),
        default=ImprovementNoteStatus.OPEN,
        nullable=False,
        index=True,
    )
    fingerprint: Mapped[str] = mapped_column(String(512), nullable=False)


class SeoTarget(Base, TimestampMixin):
    """A site the SEO agent writes documents about. Never patched by this stack."""

    __tablename__ = "seo_targets"
    __table_args__ = (UniqueConstraint("url", "brand", name="uq_seo_targets_url_brand"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    brand: Mapped[Brand] = mapped_column(existing_brand_enum(), nullable=False, index=True)
    role: Mapped[SeoTargetRole] = mapped_column(
        str_enum(SeoTargetRole), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    queries: Mapped[list[SeoQuery]] = relationship(back_populates="target")
    reviews: Mapped[list[SeoReview]] = relationship(back_populates="target")
    plans: Mapped[list[SeoPlan]] = relationship(back_populates="target")


class SeoQuery(Base, TimestampMixin):
    """Append-only queue of SEO document jobs. Rows are never deleted."""

    __tablename__ = "seo_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(128), nullable=False, default="seed")
    brand: Mapped[Brand] = mapped_column(existing_brand_enum(), nullable=False, index=True)
    kind: Mapped[SeoQueryKind] = mapped_column(
        str_enum(SeoQueryKind), nullable=False, index=True
    )
    target_id: Mapped[int | None] = mapped_column(
        ForeignKey("seo_targets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[SeoQueryStatus] = mapped_column(
        str_enum(SeoQueryStatus),
        default=SeoQueryStatus.PENDING,
        nullable=False,
        index=True,
    )
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    target: Mapped[SeoTarget | None] = relationship(back_populates="queries")


class SeoReview(Base, TimestampMixin):
    """SEO review document for humans. Evidence plus how-to-fix; never applied live."""

    __tablename__ = "seo_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int | None] = mapped_column(
        ForeignKey("seo_targets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    brand: Mapped[Brand] = mapped_column(existing_brand_enum(), nullable=False, index=True)
    kind: Mapped[SeoReviewKind] = mapped_column(
        str_enum(SeoReviewKind), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    one_thing: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    issues: Mapped[list | None] = mapped_column(JSON, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_query: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[SeoReviewStatus] = mapped_column(
        str_enum(SeoReviewStatus),
        default=SeoReviewStatus.DRAFT,
        nullable=False,
        index=True,
    )

    target: Mapped[SeoTarget | None] = relationship(back_populates="reviews")
    plans: Mapped[list[SeoPlan]] = relationship(back_populates="review")


class SeoPlan(Base, TimestampMixin):
    """SEO implementation plan for humans to apply on the target site.

    This stack never deploys, edits, or patches live pages from these documents.
    """

    __tablename__ = "seo_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int | None] = mapped_column(
        ForeignKey("seo_targets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    review_id: Mapped[int | None] = mapped_column(
        ForeignKey("seo_reviews.id", ondelete="SET NULL"), nullable=True, index=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    brand: Mapped[Brand] = mapped_column(existing_brand_enum(), nullable=False, index=True)
    kind: Mapped[SeoPlanKind] = mapped_column(
        str_enum(SeoPlanKind), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    one_thing: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    tasks: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[SeoPlanStatus] = mapped_column(
        str_enum(SeoPlanStatus),
        default=SeoPlanStatus.DRAFT,
        nullable=False,
        index=True,
    )

    target: Mapped[SeoTarget | None] = relationship(back_populates="plans")
    review: Mapped[SeoReview | None] = relationship(back_populates="plans")


class TregTool(Base, TimestampMixin):
    """A treg catalog endpoint the Agency can call for hunter/research follow-up."""

    __tablename__ = "treg_tools"

    endpoint_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    capability: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    method: Mapped[str] = mapped_column(String(16), nullable=False, default="GET")
    path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="data")
    queue_as: Mapped[str] = mapped_column(String(16), nullable=False, default="skip", index=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    cost_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_free: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    is_routed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    selectable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    input_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Journey(Base, TimestampMixin):
    """A nurture state-machine instance owned by the Nurture agent."""

    __tablename__ = "journeys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )

    template_set: Mapped[str] = mapped_column(String(128), nullable=False)
    brand: Mapped[Brand] = mapped_column(
        SAEnum(Brand), default=Brand.UNASSIGNED, nullable=False
    )
    step_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[JourneyStatus] = mapped_column(
        SAEnum(JourneyStatus), default=JourneyStatus.ACTIVE, nullable=False
    )
    stop_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    lead: Mapped[Lead] = relationship(back_populates="journeys")
