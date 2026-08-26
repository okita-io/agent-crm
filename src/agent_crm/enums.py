"""Controlled vocabularies for the CRM.

These are the values the orchestrator and every agent share. Keeping them in
one place means "what stages exist" is a single source of truth, not a string
scattered across agents.
"""

from __future__ import annotations

import enum


class Brand(str, enum.Enum):
    """The brands leads get routed to. Set by the Brand Router agent."""

    MIDNIGHTSATIN = "midnightsatin"
    CELESTIAL_NEXUS = "celestial-nexus"
    HEYBUDDY = "heybuddy"
    TACTIC_STUDIO = "tactic-studio"
    UNASSIGNED = "unassigned"


class ContactAudience(str, enum.Enum):
    """Outbound audience bucket for tactic.studio contacts (null for other brands)."""

    MARKETING = "marketing"
    INFLUENCER = "influencer"
    USER = "user"


class LeadSource(str, enum.Enum):
    """Where a lead entered the funnel. Written by Lead Intake."""

    FORM = "form"
    EMAIL = "email"
    DM = "dm"
    HUNTER = "hunter"
    CONTACT = "contact"
    MANUAL = "manual"


class Priority(str, enum.Enum):
    """Priority band assigned by the Lead Scorer on top of a numeric score."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Stage(str, enum.Enum):
    """Pipeline stages.

    Inbound happy path:
        new -> scored -> enriched -> contacted -> replied -> qualified -> won / lost

    Outbound inserts ``prospect`` before ``contacted``.
    """

    NEW = "new"
    PROSPECT = "prospect"
    SCORED = "scored"
    ENRICHED = "enriched"
    CONTACTED = "contacted"
    REPLIED = "replied"
    QUALIFIED = "qualified"
    WON = "won"
    LOST = "lost"


# Allowed forward transitions. The Pipeline Manager enforces these so agents
# cannot silently corrupt pipeline state. Terminal stages have no exits.
STAGE_TRANSITIONS: dict[Stage, set[Stage]] = {
    Stage.NEW: {Stage.SCORED, Stage.PROSPECT, Stage.LOST},
    Stage.PROSPECT: {Stage.SCORED, Stage.CONTACTED, Stage.LOST},
    Stage.SCORED: {Stage.ENRICHED, Stage.CONTACTED, Stage.LOST},
    Stage.ENRICHED: {Stage.CONTACTED, Stage.LOST},
    Stage.CONTACTED: {Stage.REPLIED, Stage.LOST},
    Stage.REPLIED: {Stage.QUALIFIED, Stage.LOST},
    Stage.QUALIFIED: {Stage.WON, Stage.LOST},
    Stage.WON: set(),
    Stage.LOST: set(),
}

TERMINAL_STAGES: set[Stage] = {Stage.WON, Stage.LOST}


class LeadStatus(str, enum.Enum):
    """Lifecycle of the lead record itself (distinct from pipeline stage)."""

    NEW = "new"
    ACTIVE = "active"
    DISQUALIFIED = "disqualified"
    ARCHIVED = "archived"


class ActivityType(str, enum.Enum):
    """Every mutation an agent makes appends one of these. Append-only history."""

    LEAD_CREATED = "lead_created"
    SCORED = "scored"
    BRAND_ROUTED = "brand_routed"
    ENRICHED = "enriched"
    STAGE_CHANGED = "stage_changed"
    OUTREACH_DRAFTED = "outreach_drafted"
    MESSAGE_SENT = "message_sent"
    MESSAGE_REPLIED = "message_replied"
    HOT_LEAD_ALERT = "hot_lead_alert"
    NOTE = "note"
    SCRAPE = "scrape"
    VERIFIED = "verified"
    ERROR = "error"


class JourneyStatus(str, enum.Enum):
    """State of a nurture journey instance."""

    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"


class AgentStatus(str, enum.Enum):
    """Live observer status for CRM agents."""

    IDLE = "idle"
    THINKING = "thinking"
    WORKING = "working"
    BLOCKED = "blocked"


class HuntQueryStatus(str, enum.Enum):
    """Lifecycle of a queued hunter search term."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class HuntResourceKind(str, enum.Enum):
    """What kind of site/resource the hunter discovered."""

    DIRECTORY = "directory"
    COMMUNITY = "community"
    NEWSLETTER = "newsletter"
    FORUM = "forum"
    LIST = "list"
    SOCIAL = "social"
    OTHER = "other"


class ResearchFindingKind(str, enum.Enum):
    """Category of a persisted research finding."""

    COMPETITOR = "competitor"
    NONPROFIT = "nonprofit"
    OTHER = "other"


class ContactKind(str, enum.Enum):
    """Whether a verification record targets an email address or a URL."""

    EMAIL = "email"
    URL = "url"


class ContactVerificationStatus(str, enum.Enum):
    """Outcome of a defensive contact check (no SMTP / no sending)."""

    VALID = "valid"
    INVALID = "invalid"
    RISKY = "risky"
    UNKNOWN = "unknown"
