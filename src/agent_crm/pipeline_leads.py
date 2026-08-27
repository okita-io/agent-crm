"""Pipeline & Leads query helpers — only VALID-verified email contacts."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import session_scope
from .enums import (
    Brand,
    ContactAudience,
    ContactKind,
    ContactVerificationStatus,
    LeadStatus,
)
from .models import ContactVerification, Lead
from .schemas import LeadOut
from .topic_relevance_store import lead_is_topically_visible


def normalize_audience(audience: ContactAudience | None) -> ContactAudience | None:
    """Map legacy ``user`` to ``end_user`` for display and filtering."""
    from .enums import CONTACT_AUDIENCE_ALIASES

    if audience is None:
        return None
    return CONTACT_AUDIENCE_ALIASES.get(audience, audience)


def lead_email_is_pipeline_visible(
    session: Session,
    lead: Lead,
) -> bool:
    """Return True when the lead's primary email has a VALID verification row."""
    if lead.status == LeadStatus.DISQUALIFIED:
        return False
    email = (lead.email or "").strip().lower()
    if not email:
        return False
    row = session.scalar(
        select(ContactVerification.status)
        .where(ContactVerification.lead_id == lead.id)
        .where(ContactVerification.contact == email)
        .where(ContactVerification.contact_kind == ContactKind.EMAIL)
        .order_by(ContactVerification.checked_at.desc())
        .limit(1)
    )
    return row == ContactVerificationStatus.VALID


def list_pipeline_leads(
    *,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    limit: int = 500,
) -> list[LeadOut]:
    """Leads whose primary email is DNS/MX verified VALID (not disqualified)."""
    with session_scope() as session:
        email_verification = (
            select(ContactVerification.lead_id)
            .where(ContactVerification.contact_kind == ContactKind.EMAIL)
            .where(ContactVerification.status == ContactVerificationStatus.VALID)
            .where(
                func.lower(func.trim(ContactVerification.contact))
                == func.lower(func.trim(Lead.email))
            )
            .correlate(Lead)
            .exists()
        )
        stmt = (
            select(Lead)
            .where(Lead.email.is_not(None))
            .where(func.length(func.trim(Lead.email)) > 0)
            .where(Lead.status != LeadStatus.DISQUALIFIED)
            .where(email_verification)
            .order_by(Lead.created_at.desc())
            .limit(limit)
        )
        if brand is not None:
            stmt = stmt.where(Lead.brand == brand)
        if audience is not None:
            normalized = normalize_audience(audience)
            legacy = (
                ContactAudience.USER
                if normalized == ContactAudience.END_USER
                else None
            )
            if legacy is not None:
                stmt = stmt.where(Lead.audience.in_([normalized, legacy]))
            else:
                stmt = stmt.where(Lead.audience == normalized)
        leads = [row for row in session.scalars(stmt) if lead_is_topically_visible(row)]
        return [LeadOut.model_validate(row) for row in leads]
