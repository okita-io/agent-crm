"""The CRM SDK: the stable interface every agent calls.

This is the "write-back into the same CRM store" contract from the brief.
Agents never touch SQL or hold a session. They call typed methods, get typed
results, and every write appends an Activity so history stays complete.

Design rules encoded here:
- Every mutation is wrapped in a single transaction (unit of work).
- Every mutation appends an Activity naming the acting agent.
- Enrichment-style operations are idempotent-friendly: they overwrite fields
  and record what happened rather than failing on repeat.
- Stage transitions are validated against ``STAGE_TRANSITIONS`` and delegated
  to the Pipeline Manager; agents cannot corrupt pipeline state.

Usage:

    crm = CRMToolkit(actor="lead_intake")
    lead = crm.create_lead(LeadCreate(source=LeadSource.FORM, email="a@b.com"))
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import session_scope
from .enums import ActivityType, AgentStatus, Brand, LeadStatus, Priority, Stage
from .errors import NotFoundError
from .models import Account, Activity, Journey, Lead, Opportunity
from .schemas import (
    AccountOut,
    ActivityOut,
    EnrichmentInput,
    JourneyOut,
    LeadCreate,
    LeadOut,
    OpportunityOut,
    ScoreInput,
)


class CRMToolkit:
    """Every agent instantiates one of these with its own ``actor`` name.

    The actor is stamped onto every Activity so the history reads like a log of
    which agent did what, in order.
    """

    def __init__(self, actor: str) -> None:
        if not actor:
            raise ValueError("actor is required (e.g. 'lead_scoring', 'human')")
        self.actor = actor

    # -- internal helpers ----------------------------------------------------

    def _get_lead(self, session: Session, lead_id: int) -> Lead:
        lead = session.get(Lead, lead_id)
        if lead is None:
            raise NotFoundError(f"Lead {lead_id} not found")
        return lead

    def _append_activity(
        self,
        session: Session,
        *,
        type: ActivityType,
        summary: str,
        lead_id: int | None = None,
        opportunity_id: int | None = None,
        payload: dict | None = None,
    ) -> Activity:
        activity = Activity(
            lead_id=lead_id,
            opportunity_id=opportunity_id,
            actor=self.actor,
            type=type,
            summary=summary,
            payload=payload,
        )
        session.add(activity)
        return activity

    # -- Lead intake (Lead Intake agent) -------------------------------------

    def create_lead(self, data: LeadCreate) -> LeadOut:
        """Create a lead and its opportunity in stage ``new``.

        The brief says intake writes a lead before anything else happens, so
        this also opens the opportunity that the rest of the roster advances.
        """
        with session_scope() as session:
            lead = Lead(
                name=data.name,
                email=data.email,
                company=data.company,
                source=data.source,
                raw_payload=data.raw_payload,
                status=LeadStatus.NEW,
                brand=Brand.UNASSIGNED,
            )
            session.add(lead)
            session.flush()  # assign lead.id

            opp = Opportunity(lead_id=lead.id, stage=Stage.NEW, brand=Brand.UNASSIGNED)
            session.add(opp)
            session.flush()

            self._append_activity(
                session,
                type=ActivityType.LEAD_CREATED,
                summary=f"Lead created from {data.source.value}",
                lead_id=lead.id,
                opportunity_id=opp.id,
                payload={"source": data.source.value},
            )
            return LeadOut.model_validate(lead)

    # -- Scoring (Lead Scorer agent) -----------------------------------------

    def record_score(self, lead_id: int, score: ScoreInput) -> LeadOut:
        with session_scope() as session:
            lead = self._get_lead(session, lead_id)
            lead.score = score.score
            lead.priority = score.priority
            if lead.status == LeadStatus.NEW:
                lead.status = LeadStatus.ACTIVE
            self._append_activity(
                session,
                type=ActivityType.SCORED,
                summary=f"Scored {score.score} ({score.priority.value})",
                lead_id=lead.id,
                payload=score.model_dump(mode="json"),
            )
            return LeadOut.model_validate(lead)

    # -- Routing (Brand Router agent) ----------------------------------------

    def route_brand(self, lead_id: int, brand: Brand) -> LeadOut:
        with session_scope() as session:
            lead = self._get_lead(session, lead_id)
            lead.brand = brand
            if lead.opportunity is not None:
                lead.opportunity.brand = brand
            self._append_activity(
                session,
                type=ActivityType.BRAND_ROUTED,
                summary=f"Routed to {brand.value}",
                lead_id=lead.id,
                payload={"brand": brand.value},
            )
            return LeadOut.model_validate(lead)

    # -- Enrichment (Research agent) -----------------------------------------

    def record_enrichment(self, lead_id: int, data: EnrichmentInput) -> LeadOut:
        """Best-effort, idempotent-friendly enrichment write.

        Re-running overwrites the summary and account fields and logs a new
        activity rather than failing, matching "enrichment must be idempotent".
        """
        with session_scope() as session:
            lead = self._get_lead(session, lead_id)
            lead.enrichment_summary = data.summary

            if data.website or data.socials:
                account = lead.account
                if account is None:
                    account = Account(name=lead.company or lead.name or f"Lead {lead.id}")
                    session.add(account)
                    session.flush()
                    lead.account_id = account.id
                    if lead.opportunity is not None:
                        lead.opportunity.account_id = account.id
                if data.website:
                    account.website = data.website
                if data.socials:
                    account.socials = data.socials

            self._append_activity(
                session,
                type=ActivityType.ENRICHED,
                summary="Enrichment written",
                lead_id=lead.id,
                payload=data.model_dump(mode="json", exclude_none=True),
            )
            return LeadOut.model_validate(lead)

    # -- Nurture (Nurture agent) ---------------------------------------------

    def start_journey(
        self,
        lead_id: int,
        template_set: str,
        *,
        next_run_at: datetime | None = None,
    ) -> JourneyOut:
        with session_scope() as session:
            lead = self._get_lead(session, lead_id)
            journey = Journey(
                lead_id=lead.id,
                template_set=template_set,
                brand=lead.brand,
                next_run_at=next_run_at,
            )
            session.add(journey)
            session.flush()
            self._append_activity(
                session,
                type=ActivityType.NOTE,
                summary=f"Journey '{template_set}' started",
                lead_id=lead.id,
                payload={"template_set": template_set},
            )
            return JourneyOut.model_validate(journey)

    # -- Generic notes / activity (any agent) --------------------------------

    def log_note(
        self,
        summary: str,
        *,
        lead_id: int | None = None,
        type: ActivityType = ActivityType.NOTE,
        payload: dict | None = None,
    ) -> ActivityOut:
        """Append a free-form activity. The catch-all every agent can use."""
        with session_scope() as session:
            if lead_id is not None:
                self._get_lead(session, lead_id)  # validate existence
            activity = self._append_activity(
                session,
                type=type,
                summary=summary,
                lead_id=lead_id,
                payload=payload,
            )
            session.flush()
            return ActivityOut.model_validate(activity)

    # -- Reads (any agent, the dashboard, analytics) -------------------------

    def get_lead(self, lead_id: int) -> LeadOut:
        with session_scope() as session:
            return LeadOut.model_validate(self._get_lead(session, lead_id))

    def get_opportunity_for_lead(self, lead_id: int) -> OpportunityOut:
        with session_scope() as session:
            opp = session.scalar(
                select(Opportunity).where(Opportunity.lead_id == lead_id)
            )
            if opp is None:
                raise NotFoundError(f"Opportunity for lead {lead_id} not found")
            return OpportunityOut.model_validate(opp)

    def get_account(self, account_id: int) -> AccountOut:
        with session_scope() as session:
            account = session.get(Account, account_id)
            if account is None:
                raise NotFoundError(f"Account {account_id} not found")
            return AccountOut.model_validate(account)

    def list_leads(
        self,
        *,
        status: LeadStatus | None = None,
        brand: Brand | None = None,
        priority: Priority | None = None,
        limit: int = 100,
    ) -> list[LeadOut]:
        with session_scope() as session:
            stmt = select(Lead).order_by(Lead.created_at.desc())
            if status is not None:
                stmt = stmt.where(Lead.status == status)
            if brand is not None:
                stmt = stmt.where(Lead.brand == brand)
            if priority is not None:
                stmt = stmt.where(Lead.priority == priority)
            stmt = stmt.limit(limit)
            return [LeadOut.model_validate(row) for row in session.scalars(stmt)]

    def list_activities(self, lead_id: int, limit: int = 200) -> list[ActivityOut]:
        with session_scope() as session:
            self._get_lead(session, lead_id)
            stmt = (
                select(Activity)
                .where(Activity.lead_id == lead_id)
                .order_by(Activity.created_at.asc())
                .limit(limit)
            )
            return [ActivityOut.model_validate(row) for row in session.scalars(stmt)]

    def record_heartbeat(
        self,
        *,
        status: AgentStatus,
        task: str | None = None,
        resource: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Publish liveness for the live agent observer."""
        from .heartbeat import record_heartbeat

        record_heartbeat(
            self.actor,
            status=status,
            task=task,
            resource=resource,
            metadata=metadata,
        )
