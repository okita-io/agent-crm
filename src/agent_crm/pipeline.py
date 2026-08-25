"""The Pipeline Manager.

The brief: "This is the only subsystem that must exist on day one." It is the
source of truth the other agents read and write. It owns:

- Stage transitions (validated against STAGE_TRANSITIONS)
- Hot-lead detection and alerts
- The weekly report the Analytics agent renders

Stage changes route through here instead of the generic toolkit so the
transition rules live in exactly one place.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import session_scope
from .enums import STAGE_TRANSITIONS, TERMINAL_STAGES, ActivityType, Priority, Stage
from .errors import InvalidStageTransition, NotFoundError
from .models import Activity, Lead, Opportunity
from .schemas import OpportunityOut


class PipelineManager:
    """Stage/state authority. Instantiate with the acting agent name."""

    def __init__(self, actor: str = "crm_manager") -> None:
        self.actor = actor

    # -- helpers -------------------------------------------------------------

    def _opp_for_lead(self, session: Session, lead_id: int) -> Opportunity:
        opp = session.scalar(select(Opportunity).where(Opportunity.lead_id == lead_id))
        if opp is None:
            raise NotFoundError(f"Opportunity for lead {lead_id} not found")
        return opp

    def _append(
        self,
        session: Session,
        *,
        lead_id: int,
        opportunity_id: int,
        type: ActivityType,
        summary: str,
        payload: dict | None = None,
    ) -> None:
        session.add(
            Activity(
                lead_id=lead_id,
                opportunity_id=opportunity_id,
                actor=self.actor,
                type=type,
                summary=summary,
                payload=payload,
            )
        )

    # -- stage transitions ---------------------------------------------------

    def allowed_transitions(self, stage: Stage) -> set[Stage]:
        return STAGE_TRANSITIONS.get(stage, set())

    def transition(
        self,
        lead_id: int,
        to_stage: Stage,
        *,
        note: str | None = None,
    ) -> OpportunityOut:
        """Move an opportunity to ``to_stage`` if the transition is legal."""
        with session_scope() as session:
            opp = self._opp_for_lead(session, lead_id)
            from_stage = opp.stage

            if from_stage == to_stage:
                return OpportunityOut.model_validate(opp)

            if to_stage not in self.allowed_transitions(from_stage):
                raise InvalidStageTransition(
                    f"Cannot move lead {lead_id} from '{from_stage.value}' "
                    f"to '{to_stage.value}'. Allowed: "
                    f"{sorted(s.value for s in self.allowed_transitions(from_stage))}"
                )

            opp.stage = to_stage
            summary = f"Stage {from_stage.value} -> {to_stage.value}"
            if note:
                summary += f": {note}"
            self._append(
                session,
                lead_id=lead_id,
                opportunity_id=opp.id,
                type=ActivityType.STAGE_CHANGED,
                summary=summary,
                payload={"from": from_stage.value, "to": to_stage.value},
            )
            return OpportunityOut.model_validate(opp)

    def set_amount(self, lead_id: int, amount: float | None) -> OpportunityOut:
        with session_scope() as session:
            opp = self._opp_for_lead(session, lead_id)
            opp.amount = amount
            return OpportunityOut.model_validate(opp)

    def set_next_action(
        self, lead_id: int, when: datetime | None
    ) -> OpportunityOut:
        with session_scope() as session:
            opp = self._opp_for_lead(session, lead_id)
            opp.next_action_at = when
            return OpportunityOut.model_validate(opp)

    # -- hot leads -----------------------------------------------------------

    def evaluate_hot(self, lead_id: int) -> bool:
        """Flag a lead hot if its score clears the threshold or it's high priority.

        Alerts by appending a HOT_LEAD_ALERT activity the first time it flips on.
        """
        threshold = get_settings().hot_lead_threshold
        with session_scope() as session:
            lead = session.get(Lead, lead_id)
            if lead is None:
                raise NotFoundError(f"Lead {lead_id} not found")
            opp = self._opp_for_lead(session, lead_id)

            is_hot = (lead.score is not None and lead.score >= threshold) or (
                lead.priority == Priority.HIGH
            )
            newly_hot = is_hot and not bool(opp.is_hot)
            opp.is_hot = 1 if is_hot else 0

            if newly_hot:
                self._append(
                    session,
                    lead_id=lead_id,
                    opportunity_id=opp.id,
                    type=ActivityType.HOT_LEAD_ALERT,
                    summary=f"Hot lead: score={lead.score}, priority="
                    f"{lead.priority.value if lead.priority else 'n/a'}",
                    payload={"threshold": threshold, "score": lead.score},
                )
            return is_hot

    # -- reporting (Analytics agent reads this) ------------------------------

    def stage_counts(self) -> dict[str, int]:
        with session_scope() as session:
            rows = session.execute(
                select(Opportunity.stage, func.count(Opportunity.id)).group_by(
                    Opportunity.stage
                )
            ).all()
            counts = {stage.value: 0 for stage in Stage}
            for stage, count in rows:
                counts[stage.value] = count
            return counts

    def hot_leads(self) -> list[OpportunityOut]:
        with session_scope() as session:
            stmt = select(Opportunity).where(Opportunity.is_hot == 1)
            return [OpportunityOut.model_validate(r) for r in session.scalars(stmt)]

    def weekly_report(self, now: datetime | None = None) -> dict:
        """The read-only weekly snapshot. Analytics renders; it does not mutate."""
        now = now or datetime.now(UTC)
        week_ago = now - timedelta(days=7)
        with session_scope() as session:
            new_leads = session.scalar(
                select(func.count(Lead.id)).where(Lead.created_at >= week_ago)
            )
            won = session.scalar(
                select(func.count(Opportunity.id)).where(
                    Opportunity.stage == Stage.WON,
                    Opportunity.updated_at >= week_ago,
                )
            )
            lost = session.scalar(
                select(func.count(Opportunity.id)).where(
                    Opportunity.stage == Stage.LOST,
                    Opportunity.updated_at >= week_ago,
                )
            )
            open_count = session.scalar(
                select(func.count(Opportunity.id)).where(
                    Opportunity.stage.notin_(TERMINAL_STAGES)
                )
            )
            hot = session.scalar(
                select(func.count(Opportunity.id)).where(Opportunity.is_hot == 1)
            )
        return {
            "generated_at": now.isoformat(),
            "window_days": 7,
            "new_leads": new_leads or 0,
            "won": won or 0,
            "lost": lost or 0,
            "open_opportunities": open_count or 0,
            "hot_leads": hot or 0,
            "stage_counts": self.stage_counts(),
        }
