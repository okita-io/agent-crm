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
    """Lead/contact qualification bucket (audience). Expandable via Alembic."""

    MARKETING = "marketing"
    INFLUENCER = "influencer"
    USER = "user"  # legacy ingest label; prefer END_USER for new rows
    END_USER = "end_user"
    B2B = "b2b"
    CLIENT = "client"


# Hunt origins and older rows may still emit ``user``; treat as end-user.
CONTACT_AUDIENCE_ALIASES: dict[ContactAudience, ContactAudience] = {
    ContactAudience.USER: ContactAudience.END_USER,
}


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


class ResearchQueryStatus(str, enum.Enum):
    """Lifecycle of a queued research search term. Rows are never deleted."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EngagementQueryStatus(str, enum.Enum):
    """Lifecycle of a queued engagement search term. Rows are never deleted."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentJobKind(str, enum.Enum):
    """Kinds of background CRM work dispatched by the job runner."""

    ENRICH_CONTACT = "enrich_contact"
    VERIFY_LEAD = "verify_lead"
    DECODE_EMAIL = "decode_email"
    QUALIFY_CONTACT = "qualify_contact"
    CHECK_TOPICAL_RELEVANCE = "check_topical_relevance"


class AgentJobStatus(str, enum.Enum):
    """Lifecycle of a queued agent job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


SPARK_AGENT_JOB_KINDS: frozenset[AgentJobKind] = frozenset(
    {
        AgentJobKind.ENRICH_CONTACT,
        AgentJobKind.DECODE_EMAIL,
        AgentJobKind.QUALIFY_CONTACT,
        AgentJobKind.CHECK_TOPICAL_RELEVANCE,
    }
)


class ImprovementNoteKind(str, enum.Enum):
    """Category of self-learning orchestration note."""

    GAP = "gap"
    PERFORMANCE = "performance"
    REPAIR = "repair"


class ImprovementNoteSeverity(str, enum.Enum):
    """How urgently a note needs attention."""

    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


class ImprovementNoteStatus(str, enum.Enum):
    """Lifecycle of an improvement note."""

    OPEN = "open"
    INVESTIGATING = "investigating"
    PATCHED = "patched"
    WONTFIX = "wontfix"


class ImprovementSourceAgent(str, enum.Enum):
    """Agent or service that raised an improvement note."""

    JOB_DISPATCHER = "job-dispatcher"
    HUNT_LOOP = "hunt-loop"
    RESEARCH_LOOP = "research-loop"
    ENGAGEMENT_LOOP = "engagement-loop"
    LEAD_VERIFIER = "lead_verifier"
    SPARK_QUEUE = "spark-queue"
    ORCHESTRATOR = "orchestrator"


class HuntResourceKind(str, enum.Enum):
    """What kind of site/resource the hunter discovered."""

    DIRECTORY = "directory"
    COMMUNITY = "community"
    NEWSLETTER = "newsletter"
    FORUM = "forum"
    LIST = "list"
    SOCIAL = "social"
    OTHER = "other"


class EngagementThreadStatus(str, enum.Enum):
    """Lifecycle of a catalogued community thread for later scans."""

    CATALOGED = "cataloged"
    QUEUED = "queued"
    SCANNED = "scanned"
    DRAFT_READY = "draft_ready"


class EngagementDraftStatus(str, enum.Enum):
    """Human-review state for a comment draft. This stack never posts."""

    DRAFT = "draft"
    REVIEW = "review"
    REJECTED = "rejected"


class ResearchFindingKind(str, enum.Enum):
    """Category of a persisted research finding."""

    COMPETITOR = "competitor"
    NONPROFIT = "nonprofit"
    AD_PLACEMENT = "ad_placement"
    OTHER = "other"


class ContactKind(str, enum.Enum):
    """Whether a verification record targets an email address or a URL."""

    EMAIL = "email"
    URL = "url"


class ContactEmailKind(str, enum.Enum):
    """Persisted contact-profile quality bucket for SQL filtering."""

    PERSON = "person"
    ROLE = "role"
    JUNK = "junk"


class ContactVerificationStatus(str, enum.Enum):
    """Outcome of a defensive contact check (no SMTP / no sending)."""

    VALID = "valid"
    INVALID = "invalid"
    RISKY = "risky"
    UNKNOWN = "unknown"


class TopicalRelevanceVerdict(str, enum.Enum):
    """Whether a URL/page is on-brand for a hunt target."""

    ON_TOPIC = "on_topic"
    OFF_TOPIC = "off_topic"
    UNCERTAIN = "uncertain"
