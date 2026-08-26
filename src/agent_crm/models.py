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
    AgentStatus,
    Brand,
    ContactAudience,
    ContactKind,
    ContactVerificationStatus,
    HuntQueryStatus,
    HuntResourceKind,
    JourneyStatus,
    LeadSource,
    LeadStatus,
    Priority,
    ResearchFindingKind,
    Stage,
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
    found_via_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    hit_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResearchFinding(Base):
    """First-class research output from the Research agent."""

    __tablename__ = "research_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True, index=True)
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
    socials: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )

    lead: Mapped[Lead | None] = relationship(back_populates="contact_profile")


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
